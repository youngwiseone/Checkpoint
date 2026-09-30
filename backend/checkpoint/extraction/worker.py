"""Background organisation runs (one at a time, sharing the heavy-work lock)."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from ..db import read_session, write_session
from ..events import notices
from ..models import ProcessingRun, Session, TranscriptionJob, utcnow
from ..settings_store import get_settings
from ..transcription.engine import model_installed
from ..workers import HEAVY_LOCK, Worker
from .ollama import OllamaProvider, ProviderError, ensure_running
from .pipeline import PROMPT_VERSION, Cancelled, Pipeline, build_sources, fingerprint_inputs

log = logging.getLogger(__name__)


def recover_runs() -> None:
    with write_session() as s:
        for r in s.scalars(select(ProcessingRun).where(ProcessingRun.state == "running")).all():
            r.state = "queued"
            r.stage = "Requeued after restart"
            r.cancel_requested = False


def queue_run(session_id: str, force: bool = True) -> str:
    settings = get_settings().ai
    if not settings.enabled:
        raise ValueError("Local AI is off. Turn it on in Settings → Local AI (it's optional).")
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise ValueError("Session not found")
        if sess.state in ("active", "paused"):
            raise ValueError("End the session before organising it.")
        existing = s.scalars(select(ProcessingRun).where(ProcessingRun.session_id == session_id,
                                                          ProcessingRun.state.in_(("queued", "running")))).first()
        if existing:
            return existing.id
        run = ProcessingRun(session_id=session_id, model=settings.model, prompt_version=PROMPT_VERSION,
                            stage="Queued", stats={"force": force})
        s.add(run)
        sess.processing_state = "queued"
        s.flush()
        return run.id


def cancel_run(run_id: str) -> None:
    with write_session() as s:
        r = s.get(ProcessingRun, run_id)
        if r and r.state in ("queued", "running"):
            r.cancel_requested = True
            if r.state == "queued":
                r.state = "cancelled"
                r.finished_at = utcnow()
                sess = s.get(Session, r.session_id)
                if sess:
                    sess.processing_state = "cancelled"


def run_dict(r: ProcessingRun) -> dict:
    return {"id": r.id, "session_id": r.session_id, "state": r.state, "model": r.model, "prompt_version": r.prompt_version,
            "progress": r.progress, "stage": r.stage, "error": r.error, "stats": r.stats or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


class ExtractionWorker(Worker):
    name = "organiser"
    idle_sleep = 3.0

    def __init__(self) -> None:
        super().__init__()
        self.current_run: Optional[str] = None
        self._provider: Optional[OllamaProvider] = None

    def _auto_queue(self) -> None:
        if not get_settings().ai.enabled:
            return
        with read_session() as s:
            ids = s.scalars(select(Session.id).where(Session.processing_state == "queued", Session.state.in_(("ended",)))).all()
        for sid in ids:
            with read_session() as s:
                has = s.scalars(select(ProcessingRun.id).where(ProcessingRun.session_id == sid, ProcessingRun.state.in_(("queued", "running")))).first()
            if not has:
                try:
                    queue_run(sid, force=False)
                except ValueError:
                    pass

    def _transcription_blocking(self, session_id: str) -> int:
        """Pending chunks that transcription can still realistically process."""
        st = get_settings().transcription
        if st.paused or not model_installed(st.model):
            return 0
        with read_session() as s:
            sess = s.get(Session, session_id)
            if sess is None or sess.transcription_mode == "off":
                return 0
            return len(s.scalars(select(TranscriptionJob.id).where(TranscriptionJob.session_id == session_id,
                                                                   TranscriptionJob.state.in_(("queued", "running")))).all())

    def step(self) -> bool:
        self._auto_queue()
        with read_session() as s:
            runs = s.scalars(select(ProcessingRun).where(ProcessingRun.state == "queued").order_by(ProcessingRun.created_at)).all()
        for run in runs:
            waiting = self._transcription_blocking(run.session_id)
            if waiting:
                with write_session() as s:
                    r = s.get(ProcessingRun, run.id)
                    if r:
                        r.stage = f"Waiting for transcription ({waiting} audio block{'s' if waiting != 1 else ''} left)"
                continue
            with HEAVY_LOCK:
                self._execute(run.id)
            return True
        return False

    def _set(self, run_id: str, **kw) -> None:  # noqa: ANN003
        with write_session() as s:
            r = s.get(ProcessingRun, run_id)
            if r:
                for k, v in kw.items():
                    setattr(r, k, v)

    def _is_cancelled(self, run_id: str) -> bool:
        with read_session() as s:
            r = s.get(ProcessingRun, run_id)
            return bool(r is None or r.cancel_requested)

    def cancel_current(self, run_id: str) -> None:
        cancel_run(run_id)
        if self.current_run == run_id and self._provider is not None:
            self._provider.close()  # aborts the in-flight HTTP request

    def _execute(self, run_id: str) -> None:
        cfg = get_settings().ai
        self.current_run = run_id
        self.busy = True
        with read_session() as s:
            run = s.get(ProcessingRun, run_id)
            session_id = run.session_id
            force = bool((run.stats or {}).get("force"))
        try:
            _ctx, timeline, checklist, _m = build_sources(session_id)
            fp = fingerprint_inputs(timeline, checklist, cfg.model)
            with read_session() as s:
                last = s.scalars(select(ProcessingRun).where(ProcessingRun.session_id == session_id, ProcessingRun.state == "done")
                                 .order_by(ProcessingRun.created_at.desc())).first()
            if not force and last is not None and last.input_fingerprint == fp:
                self._set(run_id, state="done", progress=1.0, stage="Already up to date", finished_at=utcnow(), input_fingerprint=fp)
                with write_session() as s:
                    s.get(Session, session_id).processing_state = "done"
                return
            self._set(run_id, state="running", stage="Starting", progress=0.02, input_fingerprint=fp, model=cfg.model)
            with write_session() as s:
                s.get(Session, session_id).processing_state = "running"
            self._set(run_id, stage="Starting Ollama")
            if not ensure_running(cfg.base_url):
                raise ProviderError(f"Ollama isn't running at {cfg.base_url} and couldn't be started. Install it from ollama.com or run `ollama serve`.")
            self._provider = OllamaProvider(cfg.base_url, cfg.timeout_seconds, cfg.max_retries)
            pipe = Pipeline(
                self._provider, cfg.model, cfg.chunk_chars, cfg.chunk_overlap_items, cfg.window_before_s, cfg.window_after_s,
                progress=lambda p, st: self._set(run_id, progress=p, stage=st),
                cancelled=lambda: self._is_cancelled(run_id),
            )
            stats = pipe.run(session_id, run_id)
            self._set(run_id, state="done", progress=1.0, stage="Done", stats=stats, finished_at=utcnow())
            notices.push("success", f"Organised session: {stats.get('created', 0)} new card(s), {stats.get('updated', 0)} updated.", "organiser")
        except Cancelled:
            self._set(run_id, state="cancelled", stage="Cancelled", finished_at=utcnow())
            with write_session() as s:
                s.get(Session, session_id).processing_state = "cancelled"
        except ProviderError as e:
            if self._is_cancelled(run_id):
                self._set(run_id, state="cancelled", stage="Cancelled", finished_at=utcnow())
                state = "cancelled"
            else:
                self._set(run_id, state="failed", error=str(e), stage="Failed", finished_at=utcnow())
                state = "failed"
                notices.push("error", f"Organising failed: {e}", "organiser")
            with write_session() as s:
                s.get(Session, session_id).processing_state = state
        except Exception as e:  # noqa: BLE001
            log.exception("Organisation run failed")
            self._set(run_id, state="failed", error=f"Unexpected error: {e}", stage="Failed", finished_at=utcnow())
            with write_session() as s:
                s.get(Session, session_id).processing_state = "failed"
        finally:
            if self._provider is not None:
                self._provider.close()
            self._provider = None
            self.current_run = None
            self.busy = False
