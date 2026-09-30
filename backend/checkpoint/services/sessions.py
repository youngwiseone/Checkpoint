"""Session lifecycle: start, pause/resume, end, crash recovery."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import paths
from ..db import read_session, write_session
from ..events import hooks, notices
from ..models import (
    AudioChunk,
    AudioEvent,
    AudioSource,
    ChecklistEntry,
    Project,
    Session,
    SessionPause,
    TranscriptionJob,
    utcnow,
)
from ..timebase import SessionClock
from ..audio.recorder import repair_wav

log = logging.getLogger(__name__)


class SourceConfig(BaseModel):
    enabled: bool = False
    device: Optional[str] = None
    label: str = ""


class StartSessionRequest(BaseModel):
    project_id: str
    title: Optional[str] = None
    purpose: str = ""
    checklist: list[str] = Field(default_factory=list)
    bring_in_item_ids: list[str] = Field(default_factory=list)
    mic: SourceConfig = Field(default_factory=SourceConfig)
    loopback: SourceConfig = Field(default_factory=SourceConfig)
    capture_target: str = "foreground"
    transcription_mode: str = "after"  # live | after | off
    ai_enabled: bool = False
    always_ask_context: bool = False


def remembered_setup(project_id: str) -> StartSessionRequest:
    """How the next session for a project starts: like its last session (sources, devices, labels,
    transcription, AI, screen), else like the last session of any project, else the saved defaults."""
    from ..settings_store import get_settings

    st = get_settings()
    with read_session() as s:
        last = s.scalars(select(Session).where(Session.project_id == project_id, Session.is_demo.is_(False))
                         .order_by(Session.started_at.desc())).first()
        if last is None:
            last = s.scalars(select(Session).where(Session.is_demo.is_(False)).order_by(Session.started_at.desc())).first()
        if last is None:
            a = st.audio
            mode = st.transcription.default_mode
            return StartSessionRequest(
                project_id=project_id,
                mic=SourceConfig(enabled=a.mic_enabled, device=a.mic_device, label=a.mic_label),
                loopback=SourceConfig(enabled=a.loopback_enabled, device=a.loopback_device, label=a.loopback_label),
                transcription_mode=mode if mode != "off" else "after", ai_enabled=st.ai.enabled,
                always_ask_context=st.capture.always_ask_context,
            )
        srcs = {}
        for x in s.scalars(select(AudioSource).where(AudioSource.session_id == last.id)).all():
            toggle = s.scalars(select(AudioEvent.kind).where(AudioEvent.source_id == x.id, AudioEvent.kind.in_(("enabled", "disabled")))
                               .order_by(AudioEvent.offset_ms.desc())).first()
            if toggle != "disabled":  # turned off during that session: keep it off
                srcs[x.kind] = x
        mode = last.transcription_mode
        if mode == "off":  # a session without audio says nothing about the transcription preference
            mode = st.transcription.default_mode if st.transcription.default_mode != "off" else "after"
        cfg = {kind: SourceConfig(enabled=kind in srcs, device=(srcs[kind].device_name or None) if kind in srcs else None,
                                  label=srcs[kind].label if kind in srcs else ("Me" if kind == "mic" else "Computer audio"))
               for kind in ("mic", "loopback")}
        return StartSessionRequest(
            project_id=project_id, mic=cfg["mic"], loopback=cfg["loopback"], capture_target=last.capture_target or "foreground",
            transcription_mode=mode, ai_enabled=last.ai_enabled and st.ai.enabled, always_ask_context=last.always_ask_context,
        )


@dataclass
class ActiveSession:
    id: str
    project_id: str
    title: str
    clock: SessionClock
    capture_target: str
    always_ask_context: bool
    transcription_mode: str
    ai_enabled: bool
    paused: bool = False
    recorders: dict = field(default_factory=dict)  # kind -> SourceRecorder
    pause_row_id: Optional[str] = None


class SessionManager:
    def __init__(self) -> None:
        self.active: Optional[ActiveSession] = None
        self._lock = threading.RLock()
        self._hb_stop = threading.Event()
        self._on_chunk_closed = lambda _cid: None

    def set_chunk_callback(self, cb) -> None:  # noqa: ANN001
        self._on_chunk_closed = cb

    # ------------------------------------------------------------------ start
    def start(self, req: StartSessionRequest) -> str:
        from ..settings_store import get_settings, update_settings

        with self._lock:
            if self.active is not None:
                raise ValueError("A session is already running. End it before starting another.")
            if req.transcription_mode not in ("live", "after", "off"):
                raise ValueError("Invalid transcription mode")
            ai_ok = get_settings().ai.enabled
            now = datetime.now(timezone.utc)
            title = (req.title or "").strip() or now.astimezone().strftime("Session %a %d %b %Y, %H:%M")
            with write_session() as s:
                project = s.get(Project, req.project_id)
                if project is None:
                    raise ValueError("Project not found")
                sess = Session(
                    project_id=project.id, title=title[:300], purpose=req.purpose.strip(), state="active",
                    started_at=now, last_heartbeat_at=now, capture_target=req.capture_target or "foreground",
                    transcription_mode=req.transcription_mode if (req.mic.enabled or req.loopback.enabled) else "off",
                    ai_enabled=bool(req.ai_enabled and ai_ok), always_ask_context=req.always_ask_context,
                    is_demo=project.is_demo,
                )
                s.add(sess)
                s.flush()
                for i, text in enumerate(t.strip() for t in req.checklist):
                    if text:
                        s.add(ChecklistEntry(session_id=sess.id, project_id=project.id, text=text[:1000], origin="planned", position=i))
                if req.bring_in_item_ids:
                    from ..models import WorkItem

                    items = s.scalars(select(WorkItem).where(WorkItem.id.in_(req.bring_in_item_ids), WorkItem.project_id == project.id)).all()
                    for j, it in enumerate(items):
                        s.add(ChecklistEntry(session_id=sess.id, project_id=project.id, text=it.title, work_item_id=it.id,
                                             origin="brought_in", position=len(req.checklist) + j))
                sources = []
                for kind, cfg in (("mic", req.mic), ("loopback", req.loopback)):
                    if cfg.enabled:
                        src = AudioSource(session_id=sess.id, kind=kind, device_name=cfg.device or "",
                                          label=(cfg.label or ("Me" if kind == "mic" else "Computer audio"))[:100], state="starting")
                        s.add(src)
                        s.flush()
                        sources.append((kind, src.id, cfg.device, src.label))
                session_id = sess.id
                started = sess.started_at
            update_settings({"last_project_id": req.project_id})
            active = ActiveSession(
                id=session_id, project_id=req.project_id, title=title, clock=SessionClock(started),
                capture_target=req.capture_target or "foreground", always_ask_context=req.always_ask_context,
                transcription_mode=req.transcription_mode, ai_enabled=bool(req.ai_enabled and ai_ok),
            )
            for kind, source_id, device, label in sources:
                active.recorders[kind] = self._make_recorder(active, source_id, kind, device, label)
            self.active = active
            for r in active.recorders.values():
                r.start()
        self._start_heartbeat()
        hooks.emit_state()
        return session_id

    def _make_recorder(self, active: ActiveSession, source_id: str, kind: str, device: Optional[str], label: str):  # noqa: ANN202
        from ..audio.recorder import SourceRecorder
        from ..settings_store import get_settings

        with read_session() as s:
            last_seq = s.scalar(select(func.max(AudioChunk.seq)).where(AudioChunk.source_id == source_id)) or 0
        return SourceRecorder(
            session_id=active.id, source_id=source_id, kind=kind, device_name=device or None, label=label,
            clock=active.clock, chunk_seconds=get_settings().audio.chunk_seconds, next_seq=last_seq,
            on_chunk_closed=self._on_chunk_closed,
        )

    # ------------------------------------------------------------------ pause/resume/end
    def pause(self) -> None:
        with self._lock:
            a = self.active
            if a is None or a.paused:
                return
            a.paused = True
            for r in a.recorders.values():
                r.pause()
            with write_session() as s:
                sess = s.get(Session, a.id)
                sess.state = "paused"
                row = SessionPause(session_id=a.id, start_offset_ms=a.clock.offset_ms(), reason="user")
                s.add(row)
                s.flush()
                a.pause_row_id = row.id
        hooks.emit_state()

    def resume(self) -> None:
        with self._lock:
            a = self.active
            if a is None or not a.paused:
                return
            a.paused = False
            with write_session() as s:
                sess = s.get(Session, a.id)
                sess.state = "active"
                if a.pause_row_id:
                    row = s.get(SessionPause, a.pause_row_id)
                    if row:
                        row.end_offset_ms = a.clock.offset_ms()
                a.pause_row_id = None
            for r in a.recorders.values():
                r.resume()
        hooks.emit_state()

    def end(self) -> Optional[str]:
        with self._lock:
            a = self.active
            if a is None:
                return None
            for r in a.recorders.values():
                r.stop()
            with write_session() as s:
                sess = s.get(Session, a.id)
                sess.state = "ended"
                sess.ended_at = utcnow()
                if a.pause_row_id:
                    row = s.get(SessionPause, a.pause_row_id)
                    if row and row.end_offset_ms is None:
                        row.end_offset_ms = a.clock.offset_ms()
                for src in s.scalars(select(AudioSource).where(AudioSource.session_id == a.id)).all():
                    src.state = "stopped"
                if sess.ai_enabled and sess.processing_state == "none":
                    sess.processing_state = "queued"
            self.active = None
        hooks.emit_state()
        return a.id

    def retry_source(self, kind: str) -> None:
        with self._lock:
            if self.active and kind in self.active.recorders:
                self.active.recorders[kind].retry()

    def set_transcription_mode(self, session_id: str, mode: str) -> None:
        if mode not in ("live", "after", "off"):
            raise ValueError("Invalid mode")
        with write_session() as s:
            sess = s.get(Session, session_id)
            if sess is None:
                raise ValueError("Session not found")
            sess.transcription_mode = mode
        with self._lock:
            if self.active and self.active.id == session_id:
                self.active.transcription_mode = mode

    # ------------------------------------------------------------------ live changes
    def set_source(self, kind: str, enabled: bool, device: Optional[str] = None, label: Optional[str] = None) -> None:
        """Turn an audio source on or off during the running session. The gap is recorded as an audio event."""
        from ..settings_store import get_settings

        if kind not in ("mic", "loopback"):
            raise ValueError("Unknown audio source")
        with self._lock:
            a = self.active
            if a is None:
                raise ValueError("No session is running")
            rec = a.recorders.get(kind)
            if not enabled:
                if rec is None:
                    return
                rec.stop()
                del a.recorders[kind]
                with write_session() as s:
                    src = s.get(AudioSource, rec.source_id)
                    if src:
                        src.state = "stopped"
                    s.add(AudioEvent(source_id=rec.source_id, kind="disabled", offset_ms=a.clock.offset_ms(),
                                     message="Turned off during the session."))
            else:
                if rec is not None and device is None and label is None:
                    return
                if rec is not None:  # switching device or label: restart the recorder
                    rec.stop()
                    del a.recorders[kind]
                with write_session() as s:
                    src = s.scalars(select(AudioSource).where(AudioSource.session_id == a.id, AudioSource.kind == kind)).first()
                    if src is None:
                        src = AudioSource(session_id=a.id, kind=kind, device_name=device or "",
                                          label=(label or ("Me" if kind == "mic" else "Computer audio"))[:100])
                        s.add(src)
                        s.flush()
                    else:
                        if device is not None:
                            src.device_name = device
                        if label:
                            src.label = label[:100]
                        s.add(AudioEvent(source_id=src.id, kind="enabled", offset_ms=a.clock.offset_ms(),
                                         message="Turned on during the session."))
                    src.state = "starting"
                    sess = s.get(Session, a.id)
                    if sess.transcription_mode == "off":
                        mode = get_settings().transcription.default_mode
                        sess.transcription_mode = a.transcription_mode = mode if mode != "off" else "after"
                    source_id, dev, lab = src.id, src.device_name or None, src.label
                r = self._make_recorder(a, source_id, kind, dev, lab)
                a.recorders[kind] = r
                r.start()
                if a.paused:
                    r.pause()
        hooks.emit_state()

    def set_options(self, always_ask_context: Optional[bool] = None, capture_target: Optional[str] = None) -> None:
        with self._lock:
            a = self.active
            if a is None:
                raise ValueError("No session is running")
            with write_session() as s:
                sess = s.get(Session, a.id)
                if always_ask_context is not None:
                    sess.always_ask_context = a.always_ask_context = always_ask_context
                if capture_target:
                    sess.capture_target = a.capture_target = capture_target
        hooks.emit_state()

    # ------------------------------------------------------------------ heartbeat & recovery
    def _start_heartbeat(self) -> None:
        if getattr(self, "_hb_thread", None) and self._hb_thread.is_alive():
            return
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat, daemon=True, name="session-heartbeat")
        self._hb_thread.start()

    def _heartbeat(self) -> None:
        while not self._hb_stop.wait(5):
            a = self.active
            if a is None:
                continue
            try:
                with write_session() as s:
                    sess = s.get(Session, a.id)
                    if sess:
                        sess.last_heartbeat_at = utcnow()
            except Exception:  # noqa: BLE001
                log.exception("heartbeat failed")

    def shutdown(self) -> None:
        """Clean application exit: an open session is ended properly."""
        self._hb_stop.set()
        if self.active is not None:
            self.end()

    def recover_on_launch(self) -> list[str]:
        """Mark sessions that were running when the process died as interrupted.

        Chunk files are repaired so already-written audio stays usable; the gap is
        recorded honestly. Microphone recording is never restarted automatically.
        """
        recovered = []
        with write_session() as s:
            stale = s.scalars(select(Session).where(Session.state.in_(("active", "paused")))).all()
            for sess in stale:
                last = sess.last_heartbeat_at or sess.started_at
                last_ms = SessionClock.offset_for_utc(sess.started_at, last)
                sess.state = "interrupted"
                open_pause = s.scalars(select(SessionPause).where(SessionPause.session_id == sess.id, SessionPause.end_offset_ms.is_(None))).first()
                if open_pause is None:
                    s.add(SessionPause(session_id=sess.id, start_offset_ms=last_ms, reason="restart"))
                for src in s.scalars(select(AudioSource).where(AudioSource.session_id == sess.id)).all():
                    if src.state not in ("stopped",):
                        s.add(AudioEvent(source_id=src.id, kind="lost_on_crash", offset_ms=last_ms,
                                         message="Recording stopped because the app closed unexpectedly. Audio after this point was not captured."))
                    src.state = "stopped"
                recovered.append(sess.id)
            chunks = s.scalars(select(AudioChunk).where(AudioChunk.state == "recording")).all()
            for c in chunks:
                p = paths().audio / c.rel_path
                try:
                    rate, ch, frames = repair_wav(p)
                    c.duration_ms = int(frames * 1000 / rate) if rate else 0
                    c.state = "recovered" if frames > 0 else "missing"
                    if frames > 0 and s.scalars(select(TranscriptionJob).where(TranscriptionJob.chunk_id == c.id)).first() is None:
                        s.add(TranscriptionJob(session_id=c.session_id, chunk_id=c.id))
                except (OSError, ValueError):
                    c.state = "missing"
        if recovered:
            notices.push("warning", f"Recovered {len(recovered)} session(s) that were interrupted. Saved captures and audio are intact up to the interruption.", "recovery")
        return recovered

    def resume_interrupted(self, session_id: str, req: StartSessionRequest) -> str:
        """Continue an interrupted session (user action). Audio sources follow the new request only."""
        with self._lock:
            if self.active is not None:
                raise ValueError("Another session is running.")
            with write_session() as s:
                sess = s.get(Session, session_id)
                if sess is None or sess.state not in ("interrupted",):
                    raise ValueError("Only interrupted sessions can be resumed")
                sess.state = "active"
                clock = SessionClock(sess.started_at)
                open_pause = s.scalars(select(SessionPause).where(SessionPause.session_id == sess.id, SessionPause.end_offset_ms.is_(None))).first()
                if open_pause:
                    open_pause.end_offset_ms = clock.offset_ms()
                sources = []
                existing = {x.kind: x for x in s.scalars(select(AudioSource).where(AudioSource.session_id == sess.id)).all()}
                for kind, cfg in (("mic", req.mic), ("loopback", req.loopback)):
                    if cfg.enabled:
                        src = existing.get(kind)
                        if src is None:
                            src = AudioSource(session_id=sess.id, kind=kind, device_name=cfg.device or "",
                                              label=cfg.label or ("Me" if kind == "mic" else "Computer audio"))
                            s.add(src)
                            s.flush()
                        src.device_name = cfg.device or ""
                        src.state = "starting"
                        sources.append((kind, src.id, cfg.device, src.label))
                active = ActiveSession(id=sess.id, project_id=sess.project_id, title=sess.title, clock=clock,
                                       capture_target=sess.capture_target, always_ask_context=sess.always_ask_context,
                                       transcription_mode=sess.transcription_mode, ai_enabled=sess.ai_enabled)
            for kind, source_id, device, label in sources:
                active.recorders[kind] = self._make_recorder(active, source_id, kind, device, label)
            self.active = active
            for r in active.recorders.values():
                r.start()
        self._start_heartbeat()
        hooks.emit_state()
        return session_id

    def end_interrupted(self, session_id: str) -> None:
        with write_session() as s:
            sess = s.get(Session, session_id)
            if sess is None or sess.state != "interrupted":
                raise ValueError("Session is not interrupted")
            sess.state = "ended"
            sess.ended_at = sess.last_heartbeat_at or utcnow()
            if sess.ai_enabled and sess.processing_state == "none":
                sess.processing_state = "queued"

    # ------------------------------------------------------------------ status
    def audio_functioning(self) -> bool:
        a = self.active
        if a is None or a.paused:
            return False
        return any(r.state in ("recording", "idle", "starting") for r in a.recorders.values())

    def status(self) -> dict:
        a = self.active
        if a is None:
            return {"active": False}
        return {
            "active": True,
            "session_id": a.id,
            "project_id": a.project_id,
            "title": a.title,
            "paused": a.paused,
            "offset_ms": a.clock.offset_ms(),
            "started_at": a.clock.started_at.isoformat(),
            "transcription_mode": a.transcription_mode,
            "ai_enabled": a.ai_enabled,
            "capture_target": a.capture_target,
            "always_ask_context": a.always_ask_context,
            "audio_functioning": self.audio_functioning(),
            "sources": [r.status() for r in a.recorders.values()],
        }


def stale_recorder_check(manager: SessionManager) -> None:
    """Supervisor hook: a recorder whose writer died is surfaced (never silently healthy)."""
    a = manager.active
    if a is None:
        return
    for r in a.recorders.values():
        if not r.alive and r.state not in ("stopped",):
            r.state = "failed"
            r.error = r.error or "Recorder thread stopped unexpectedly"
        elif r.alive and time.monotonic() - r.heartbeat > 10 and r.state not in ("paused", "failed", "reconnecting"):
            r.error = "Recorder appears stalled"
