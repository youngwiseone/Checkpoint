"""Auto-capture: a local model reads the live transcript and decides when to take a screenshot.

Live transcription arrives 20-40 s after the words were spoken, so a screenshot taken
when the text appears would show the wrong moment. While auto-capture is watching, a
buffer grabs the screen every couple of seconds and keeps the last ~90 s in memory
only; when the model picks a transcript line, the frame from when it was said is saved
as an ordinary audio marker. Frames that aren't picked never touch the disk.

Whisper lines are long (up to a whole 20 s audio block), so a picked line is narrowed to the
sentence the model rates highest and the frame is taken from just before that sentence was
said. Short filler, low-confidence lines and known Whisper hallucinations are never asked
about, picks close to a manual screenshot are skipped, and a cooldown spaces shots out.

The decision comes from a System One (Jev-style) model through Ollama's /v1/systemone
endpoint: it returns the probability that a screenshot is worth taking and never writes text.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import Optional

from sqlalchemy import select

from ..db import read_session
from ..events import notices
from ..models import Capture, TranscriptSegment
from ..settings_store import get_settings
from ..workers import HEAVY_LOCK, Worker

log = logging.getLogger(__name__)

# Short and specific works best for the small decision models (tev1:0.8b scored 10/10 on a sample set with this).
QUESTION = "Is the speaker pointing out a bug, glitch or problem they can see on screen?"
MIN_WORDS = 3
MANUAL_NEARBY_S = 20  # a hotkey screenshot this close already covers the moment
# Whisper invents these on silence or music; they're never about the screen.
HALLUCINATIONS = ("subscribe", "thanks for watching", "thank you for watching", "see you next time", "like and subscribe")
_SENTENCE = re.compile(r"[^.?!]+[.?!]*")


def worth_asking(text: str, low_confidence: bool = False) -> bool:
    t = text.strip().lower()
    return not low_confidence and len(t.split()) >= MIN_WORDS and not any(h in t for h in HALLUCINATIONS)


def sentences(text: str) -> list[tuple[int, str]]:
    """(start character, sentence) for sentences long enough to judge on their own."""
    return [(m.start() + len(m.group()) - len(m.group().lstrip()), m.group().strip())
            for m in _SENTENCE.finditer(text) if len(m.group().split()) >= MIN_WORDS]


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
        self.last_shot_ms: Optional[int] = None
        self.state = "off"  # off | needs_live | watching
        self.model_error: Optional[str] = None
        self._provider = None

    def _decider(self, base_url: str):  # noqa: ANN202
        from ..extraction.ollama import OllamaProvider

        if self._provider is None or self._provider.base_url != base_url.rstrip("/"):
            self._provider = OllamaProvider(base_url, timeout=120, max_retries=1)  # first call loads the model
        return self._provider

    def score(self, text: str) -> float:
        st = get_settings()
        return self._decider(st.ai.base_url).noul(st.auto_capture.model, {"transcript_line": text}, QUESTION)

    def decide(self, text: str) -> bool:
        return self.score(text) >= get_settings().auto_capture.threshold

    def locate(self, text: str) -> tuple[str, float]:
        """The sentence the remark is in, and how far into the line it starts (0-1)."""
        parts = sentences(text)
        if len(parts) < 2:
            return text.strip(), 0.0
        best = max(parts, key=lambda p: self.score(p[1]))
        return best[1], best[0] / max(1, len(text))

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
            self.session_id, self.seen, self.count, self.last_shot_ms = a.id, set(), 0, None
            from ..extraction.ollama import ensure_running

            ensure_running(st.ai.base_url)
            self.frames.start(a.capture_target)
            try:  # loading the model takes ~30 s the first time; do it before the first transcript arrives
                self.decide("warm up")
            except Exception:  # noqa: BLE001 - reported when a real line is decided
                pass
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
            rows = [(g.id, g.start_ms, g.end_ms, (g.corrected_text or g.text).strip(), g.low_confidence) for g in segs]
        for seg_id, start_ms, end_ms, text, low in rows:
            if seg_id in self.seen:
                continue
            if not worth_asking(text, low):
                self.seen.add(seg_id)
                continue
            if not HEAVY_LOCK.acquire(timeout=10):
                return False  # transcription is busy; try again on the next step
            try:
                pick = self.decide(text)
                sentence, frac = self.locate(text) if pick else ("", 0.0)
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
            if pick:
                said_ms = start_ms + int(frac * max(0, (end_ms or start_ms) - start_ms))
                self._shoot(a, said_ms, sentence, st.auto_capture)
        return False

    def _manual_nearby(self, session_id: str, at_ms: int) -> bool:
        with read_session() as s:
            return s.scalars(select(Capture.id).where(
                Capture.session_id == session_id, Capture.trigger != "auto", Capture.status != "discarded",
                Capture.offset_ms.between(at_ms - MANUAL_NEARBY_S * 1000, at_ms + MANUAL_NEARBY_S * 1000))).first() is not None

    def _shoot(self, a, said_ms: int, sentence: str, cfg) -> None:  # noqa: ANN001
        at_ms = said_ms - int(cfg.lead_s * 1000)
        if self.count >= cfg.max_per_session:
            return
        if self.last_shot_ms is not None and abs(at_ms - self.last_shot_ms) < cfg.cooldown_s * 1000:
            return
        if self._manual_nearby(a.id, at_ms):
            return
        # Map the transcript offset back to the monotonic clock the frames were stamped with.
        mono = time.monotonic() - (a.clock.offset_ms() - at_ms) / 1000
        frame = self.frames.nearest(mono, max(3.0, cfg.frame_interval_s * 1.5))
        if frame is None:
            return
        try:
            self.capture.auto_capture(frame, reason=sentence)
        except Exception:  # noqa: BLE001
            log.exception("Saving an auto screenshot failed")
            return
        self.count += 1
        self.last_shot_ms = at_ms

    def status(self) -> dict:
        return {"alive": self.alive, "enabled": get_settings().auto_capture.enabled, "state": self.state, "count": self.count,
                "error": self.model_error or self.frames.error or self.last_error}
