"""Screenshot and quick-note capture.

Order of operations on a hotkey is fixed: grab pixels + timestamp → persist →
only then show any window or toast. Capture never waits on transcription, AI or
the network.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from ..config import paths
from ..db import read_session, write_session
from ..events import hooks, notices
from ..models import Capture, DraftItem, EvidenceLink, Note, Project, Session, new_id
from ..settings_store import get_settings, update_settings
from ..storage import atomic_write_bytes, media_path, sha256_bytes
from ..timebase import SessionClock, fmt_offset

log = logging.getLogger(__name__)

CATEGORIES = ("bug", "improvement", "idea", "task", "question", "note")

_BUG = re.compile(r"\b(bug|broken|crash(es|ed)?|error|wrong|doesn'?t work|not working|fails?|glitch|stuck|freez(e|es)|missing|incorrect|shouldn'?t|should not)\b", re.I)
_TASK = re.compile(r"^\s*(remember to|todo|to do|check|need to|must|don'?t forget|make sure|add|fix|update|verify|test)\b", re.I)
_IMPROVE = re.compile(r"\b(should|instead|could be|would be better|improve|better|clearer|faster|easier|use the)\b", re.I)
_IDEA = re.compile(r"\b(what if|maybe|idea|might be (nice|cool|fun)|how about)\b", re.I)


def guess_type(text: str) -> str:
    """Deterministic, editable first guess for typed notes (no model involved)."""
    t = text.strip()
    first = t.splitlines()[0] if t else ""
    if first.endswith("?"):
        return "question"
    if _TASK.search(first):
        return "task"
    if _BUG.search(t):
        return "bug"
    if _IDEA.search(t):
        return "idea"
    if _IMPROVE.search(t):
        return "improvement"
    return "note"


def provisional_title(text: str) -> str:
    first = text.strip().splitlines()[0].strip() if text.strip() else "Untitled"
    return first if len(first) <= 120 else first[:117].rstrip() + "…"


def default_project_id() -> str:
    """Captures outside a session go to the project whose program is in the foreground, else the last
    used project, else a created 'General' project."""
    pid = None
    try:
        from ..capture.win32 import foreground_window
        from .flow import project_for_window

        pid = project_for_window(foreground_window())
    except Exception:  # noqa: BLE001 - inference is a convenience, never a reason to lose a capture
        pid = None
    pid = pid or get_settings().last_project_id
    with write_session() as s:
        if pid and s.get(Project, pid) is not None:
            return pid
        p = s.scalars(select(Project).where(Project.is_demo.is_(False), Project.archived.is_(False)).order_by(Project.created_at)).first()
        if p is None:
            p = Project(name="General", description="Captures made outside a session.")
            s.add(p)
            s.flush()
        pid = p.id
    update_settings({"last_project_id": pid})
    return pid


class CaptureService:
    def __init__(self, sessions) -> None:  # noqa: ANN001
        self.sessions = sessions
        self._last_press: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ hotkeys
    def debounce(self, action: str) -> bool:
        """True when the press should be ignored."""
        ms = get_settings().capture.debounce_ms
        now = time.monotonic()
        with self._lock:
            last = self._last_press.get(action, 0)
            if (now - last) * 1000 < ms:
                return True
            self._last_press[action] = now
            return False

    def hotkey_capture(self, ask_context: bool) -> Optional[dict]:
        """Runs on the hotkey thread. Returns the capture payload or None on failure."""
        from ..capture import screen

        mono = time.monotonic()
        taken_at = datetime.now(timezone.utc)
        active = self.sessions.active
        target = active.capture_target if active else "foreground"
        try:
            g = screen.grab(target, get_settings().capture.region)
        except Exception as e:  # noqa: BLE001
            log.exception("Screenshot failed")
            msg = f"Screenshot failed: {e}"
            notices.push("error", msg, "capture")
            hooks.emit_toast("error", msg)
            return None
        settings = get_settings()
        audio_ok = self.sessions.audio_functioning()
        needs_note = (
            ask_context
            or settings.capture.always_ask_context
            or bool(active and active.always_ask_context)
            or not audio_ok
        )
        try:
            cap = self._persist(g, taken_at, mono, "hotkey_context" if ask_context else "hotkey", "unfinished" if needs_note else "marker")
        except Exception as e:  # noqa: BLE001
            log.exception("Saving screenshot failed")
            msg = f"The screenshot couldn't be saved: {e}"
            notices.push("error", msg, "capture")
            hooks.emit_toast("error", msg)
            return None
        for w in g.warnings:
            notices.push("warning", w, "capture")
        payload = {
            **cap,
            "fg_hwnd": g.fg_hwnd,
            "work_rect": g.work_rect,
            "thumb_path": str(media_path(cap["thumb_rel_path"])),
            "warnings": g.warnings,
            "needs_note": needs_note,
            "reason": "asked" if (ask_context or settings.capture.always_ask_context or (active and active.always_ask_context)) else "no_audio",
        }
        if needs_note:
            if hooks.request_note:
                hooks.request_note(payload)
            else:
                notices.push("info", "Screenshot saved to Unfinished captures — add context from the app.", "capture")
        else:
            label = fmt_offset(cap["offset_ms"])
            hint = f" · becomes a card once transcribed ({settings.hotkeys.review} to review)"
            hooks.emit_toast("success" if not g.warnings else "warning",
                             g.warnings[0] if g.warnings else f"Screenshot saved at {label}{hint}", g.work_rect)
        hooks.emit_state()
        return payload

    def auto_capture(self, frame, reason: str = "") -> Optional[dict]:  # noqa: ANN001
        """Saves a buffered frame the auto-capture model picked, as an audio marker (like F8 with audio on)."""
        from ..capture import screen

        active = self.sessions.active
        if active is None:
            return None
        g = screen.frame_to_grab(frame)
        cap = self._persist(g, active.clock.utc_at(frame.mono), frame.mono, "auto", "marker", reason=reason or None)
        short = reason if len(reason) <= 70 else reason[:70].rstrip() + "\u2026"
        quote = f" \u00b7 \u201c{short}\u201d" if reason else ""
        hooks.emit_toast("success", f"Auto screenshot at {fmt_offset(cap['offset_ms'])}{quote}", g.work_rect)
        hooks.emit_state()
        return cap

    def _persist(self, g, taken_at: datetime, mono: float, trigger: str, status: str, reason: Optional[str] = None) -> dict:  # noqa: ANN001
        active = self.sessions.active
        project_id = active.project_id if active else default_project_id()
        session_id = active.id if active else None
        offset = active.clock.offset_ms(mono) if active else None
        cid = new_id()
        folder = session_id or f"project-{project_id}"
        img_rel = f"{folder}/{cid}.png"
        thumb_rel = f"{folder}/{cid}_thumb.jpg"
        img_p, thumb_p = media_path(img_rel), media_path(thumb_rel)
        atomic_write_bytes(img_p, g.png)
        atomic_write_bytes(thumb_p, g.thumb_jpeg)
        try:
            with write_session() as s:
                s.add(Capture(
                    id=cid, session_id=session_id, project_id=project_id, taken_at=taken_at, offset_ms=offset,
                    image_rel_path=img_rel, thumb_rel_path=thumb_rel, sha256=sha256_bytes(g.png), width=g.width,
                    height=g.height, monitor=g.monitor, window_title=g.window_title, trigger=trigger, status=status, reason=reason,
                ))
        except Exception:
            for p in (img_p, thumb_p):
                try:
                    p.unlink()
                except OSError:
                    pass
            raise
        return {"id": cid, "session_id": session_id, "project_id": project_id, "offset_ms": offset,
                "taken_at": taken_at.isoformat(), "thumb_rel_path": thumb_rel, "image_rel_path": img_rel}

    def import_image(self, png: bytes, thumb: bytes, width: int, height: int, *, project_id: str, session_id: Optional[str],
                     offset_ms: Optional[int], taken_at: datetime, status: str, trigger: str = "demo") -> str:
        cid = new_id()
        folder = session_id or f"project-{project_id}"
        img_rel, thumb_rel = f"{folder}/{cid}.png", f"{folder}/{cid}_thumb.jpg"
        atomic_write_bytes(media_path(img_rel), png)
        atomic_write_bytes(media_path(thumb_rel), thumb)
        with write_session() as s:
            s.add(Capture(id=cid, session_id=session_id, project_id=project_id, taken_at=taken_at, offset_ms=offset_ms,
                          image_rel_path=img_rel, thumb_rel_path=thumb_rel, sha256=sha256_bytes(png), width=width, height=height,
                          monitor={}, window_title="", trigger=trigger, status=status))
        return cid

    # ------------------------------------------------------------ notes
    def save_capture_note(self, capture_id: str, text: str, category: Optional[str] = None) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("Write a short note before saving, or press Escape to keep the screenshot for later.")
        if category and category not in CATEGORIES:
            raise ValueError("Unknown category")
        with write_session() as s:
            cap = s.get(Capture, capture_id)
            if cap is None or cap.status == "discarded":
                raise ValueError("That screenshot no longer exists")
            if not media_path(cap.image_rel_path).is_file():
                raise ValueError("The screenshot file is missing on disk")
            note = Note(session_id=cap.session_id, project_id=cap.project_id, capture_id=cap.id, text=text, category=category,
                        kind="typed", taken_at=cap.taken_at, offset_ms=cap.offset_ms)
            s.add(note)
            s.flush()
            draft = DraftItem(project_id=cap.project_id, session_id=cap.session_id, origin="typed",
                              type=category or guess_type(text), title=provisional_title(text), description=text)
            s.add(draft)
            s.flush()
            s.add(EvidenceLink(draft_id=draft.id, capture_id=cap.id, confidence="direct", attached_by="user"))
            s.add(EvidenceLink(draft_id=draft.id, note_id=note.id, confidence="direct", attached_by="user"))
            cap.status = "saved"
            cap.needs_context = False
            result = {"draft_id": draft.id, "note_id": note.id}
        hooks.emit_state()
        return result

    def quick_note(self, text: str, category: Optional[str] = None, *, mono: Optional[float] = None,
                   taken_at: Optional[datetime] = None) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("Write a short note before saving.")
        if category and category not in CATEGORIES:
            raise ValueError("Unknown category")
        active = self.sessions.active
        project_id = active.project_id if active else default_project_id()
        taken_at = taken_at or datetime.now(timezone.utc)
        offset = active.clock.offset_ms(mono) if active else None
        with write_session() as s:
            note = Note(session_id=active.id if active else None, project_id=project_id, text=text, category=category,
                        kind="quick", taken_at=taken_at, offset_ms=offset)
            s.add(note)
            s.flush()
            draft = DraftItem(project_id=project_id, session_id=note.session_id, origin="quick",
                              type=category or guess_type(text), title=provisional_title(text), description=text)
            s.add(draft)
            s.flush()
            s.add(EvidenceLink(draft_id=draft.id, note_id=note.id, confidence="direct", attached_by="user"))
            result = {"draft_id": draft.id, "note_id": note.id, "offset_ms": offset}
        hooks.emit_state()
        return result

    def keep_unfinished(self, capture_id: str) -> None:
        """Escape in the note window: keep the screenshot, don't invent an issue."""
        with write_session() as s:
            cap = s.get(Capture, capture_id)
            if cap and cap.status == "unfinished":
                cap.needs_context = True
        hooks.emit_state()

    def discard(self, capture_id: str) -> None:
        with write_session() as s:
            cap = s.get(Capture, capture_id)
            if cap is None:
                return
            linked = s.scalars(select(EvidenceLink).where(EvidenceLink.capture_id == capture_id)).first()
            if linked is not None:
                raise ValueError("This screenshot is used as evidence on a card. Detach it first.")
            img, thumb = cap.image_rel_path, cap.thumb_rel_path
            s.delete(cap)
        for rel in (img, thumb):
            try:
                media_path(rel).unlink()
            except OSError:
                pass
        hooks.emit_state()


def capture_context_state(session: Optional[Session], cap: Capture, before_s: int, after_s: int, s) -> str:  # noqa: ANN001
    """How much context a capture has. Distinguishes 'still transcribing' from 'silence'."""
    from ..models import AudioChunk, AudioSource, TranscriptionJob, TranscriptSegment

    if cap.status == "saved":
        return "typed"
    if cap.status in ("unfinished",):
        return "unfinished"
    if session is None or cap.offset_ms is None:
        return "no_audio"
    has_sources = s.scalars(select(AudioSource.id).where(AudioSource.session_id == session.id)).first()
    if not has_sources:
        return "no_audio"
    lo, hi = cap.offset_ms - before_s * 1000, cap.offset_ms + after_s * 1000
    seg = s.scalars(select(TranscriptSegment.id).where(TranscriptSegment.session_id == session.id,
                                                        TranscriptSegment.end_ms >= lo, TranscriptSegment.start_ms <= hi)).first()
    if seg:
        return "speech_nearby"
    pending = s.scalars(
        select(TranscriptionJob.id).join(AudioChunk, AudioChunk.id == TranscriptionJob.chunk_id)
        .where(TranscriptionJob.session_id == session.id, TranscriptionJob.state.in_(("queued", "running")),
               AudioChunk.start_offset_ms <= hi, (AudioChunk.start_offset_ms + AudioChunk.duration_ms) >= lo)
    ).first()
    if pending:
        return "awaiting_transcription"
    if session.state in ("active", "paused"):
        recording = s.scalars(select(AudioChunk.id).where(AudioChunk.session_id == session.id, AudioChunk.state == "recording")).first()
        if recording:
            return "awaiting_transcription"
    failed = s.scalars(
        select(TranscriptionJob.id).join(AudioChunk, AudioChunk.id == TranscriptionJob.chunk_id)
        .where(TranscriptionJob.session_id == session.id, TranscriptionJob.state == "failed",
               AudioChunk.start_offset_ms <= hi, (AudioChunk.start_offset_ms + AudioChunk.duration_ms) >= lo)
    ).first()
    if failed:
        return "transcription_failed"
    return "needs_context"
