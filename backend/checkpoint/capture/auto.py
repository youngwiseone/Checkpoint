"""Auto-capture: a local model reads the live transcript and decides when to take a screenshot.

Live transcription arrives 20-40 s after the words were spoken, so a screenshot taken
when the text appears would show the wrong moment. While auto-capture is watching, a
buffer grabs the screen every couple of seconds and keeps the last ~90 s in memory
only; when the model picks a transcript line, the frame from when it was said is saved
as an ordinary audio marker. Frames that aren't picked never touch the disk.

The model only makes a decision (capture or skip); it never writes text.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Literal, Optional

from pydantic import BaseModel
from sqlalchemy import select

from ..db import read_session
from ..events import notices
from ..models import TranscriptSegment
from ..settings_store import get_settings
from ..workers import HEAVY_LOCK, Worker

log = logging.getLogger(__name__)

SYSTEM = (
    "You watch the live transcript of someone playtesting a game or reviewing software. Decide whether what was just said "
    "is worth a screenshot of their screen right now. Choose capture for: a bug, glitch, crash, something broken, wrong or "
    "confusing; a surprising or notable moment on screen; or the speaker asking to look at, note or remember something. "
    "Choose skip for small talk, planning, narration of routine play, or anything not about what is on screen."
)


class Decision(BaseModel):
    decision: Literal["capture", "skip"]


class FrameBuffer:
    """Grabs the screen at a fixed interval into a bounded in-memory ring."""

    def __init__(self) -> None:
        self._frames: deque = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.target = "foreground"
        self.error: Optional[str] = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, target: str) -> None:
        self.target = target
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="auto-capture-frames")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(3)
        self._thread = None
        with self._lock:
            self._frames.clear()

    def add(self, frame) -> None:  # noqa: ANN001
        keep = get_settings().auto_capture.buffer_seconds
        with self._lock:
            self._frames.append(frame)
            while self._frames and frame.mono - self._frames[0].mono > keep:
                self._frames.popleft()

    def nearest(self, mono: float, tolerance_s: float):  # noqa: ANN201
        with self._lock:
            best = min(self._frames, key=lambda f: abs(f.mono - mono), default=None)
        return best if best is not None and abs(best.mono - mono) <= tolerance_s else None

    def oldest(self) -> Optional[float]:
        with self._lock:
            return self._frames[0].mono if self._frames else None

    def _run(self) -> None:
        from . import screen

        while not self._stop.is_set():
            st = get_settings()
            started = time.monotonic()
            try:
                self.add(screen.grab_frame(started, self.target, st.capture.region))
                self.error = None
            except Exception as e:  # noqa: BLE001 - a failed grab just leaves a gap
                self.error = f"Screen grab failed: {e}"
            self._stop.wait(max(0.5, st.auto_capture.frame_interval_s - (time.monotonic() - started)))


class AutoCaptureWorker(Worker):
    name = "auto-capture"
    idle_sleep = 1.0

    def __init__(self, sessions, capture) -> None:  # noqa: ANN001
        super().__init__()
        self.sessions = sessions
        self.capture = capture
        self.frames = FrameBuffer()
        self.session_id: Optional[str] = None
        self.seen: set[str] = set()
        self.count = 0
        self.last_shot = 0.0
        self.state = "off"  # off | needs_live | watching
        self.model_error: Optional[str] = None
        self._provider = None

    def _decider(self, base_url: str):  # noqa: ANN202
        from ..extraction.ollama import OllamaProvider

        if self._provider is None or self._provider.base_url != base_url.rstrip("/"):
            self._provider = OllamaProvider(base_url, timeout=30, max_retries=1)
        return self._provider

    def decide(self, text: str, before: str) -> bool:
        st = get_settings()
        user = (f"Said just before: {before}\n" if before else "") + f"Just said: {text}"
        d = self._decider(st.ai.base_url).chat_json(st.auto_capture.model, SYSTEM, user, Decision, num_ctx=1024)
        return d.decision == "capture"

    def _idle(self, state: str) -> bool:
        if self.frames.running:
            self.frames.stop()
        self.state = state
        return False

    def step(self) -> bool:
        st = get_settings()
        a = self.sessions.active
        if not st.auto_capture.enabled or a is None:
            return self._idle("off")
        if a.transcription_mode != "live":
            return self._idle("needs_live")
        if a.paused:
            return self._idle("watching")
        if a.id != self.session_id:
            self.session_id, self.seen, self.count, self.last_shot = a.id, set(), 0, 0.0
            from ..extraction.ollama import ensure_running

            ensure_running(st.ai.base_url)
        self.state = "watching"
        self.frames.start(a.capture_target)
        oldest = self.frames.oldest()
        if oldest is None:
            return False
        with read_session() as s:
            segs = s.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.session_id == a.id, TranscriptSegment.start_ms >= a.clock.offset_ms(oldest))
                .order_by(TranscriptSegment.start_ms)
            ).all()
            rows = [(g.id, g.start_ms, (g.corrected_text or g.text).strip()) for g in segs]
        before = ""
        for seg_id, start_ms, text in rows:
            if seg_id in self.seen:
                before = text
                continue
            if not HEAVY_LOCK.acquire(timeout=10):
                return False  # transcription is busy; try again on the next step
            try:
                pick = bool(text) and self.decide(text, before)
                self.model_error = None
            except Exception as e:  # noqa: BLE001
                msg = f"Auto-capture couldn't ask the local model: {e}"
                if msg != self.model_error:
                    notices.push("warning", msg, "auto-capture")
                self.model_error = msg
                return False
            finally:
                HEAVY_LOCK.release()
            self.seen.add(seg_id)
            before = text
            if pick:
                self._shoot(a, start_ms, st.auto_capture)
        return False

    def _shoot(self, a, start_ms: int, cfg) -> None:  # noqa: ANN001
        # Map the transcript offset back to the monotonic clock the frames were stamped with.
        mono = time.monotonic() - (a.clock.offset_ms() - start_ms) / 1000
        if mono - self.last_shot < cfg.cooldown_s or self.count >= cfg.max_per_session:
            return
        frame = self.frames.nearest(mono, max(3.0, cfg.frame_interval_s * 1.5))
        if frame is None:
            return
        try:
            self.capture.auto_capture(frame)
        except Exception:  # noqa: BLE001
            log.exception("Saving an auto screenshot failed")
            return
        self.count += 1
        self.last_shot = mono

    def status(self) -> dict:
        return {"alive": self.alive, "enabled": get_settings().auto_capture.enabled, "state": self.state, "count": self.count,
                "error": self.model_error or self.frames.error or self.last_error}
