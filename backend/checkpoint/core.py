"""Application core: wires persistence, capture, audio, workers and sharing.

Used by the desktop host (with tray, hotkeys and native windows) and headless for
development/tests. The UI layers talk to this object; it never imports Qt.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from typing import Optional

from .config import paths
from .db import migrate
from .events import hooks, notices

log = logging.getLogger("checkpoint")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # never log tokens/cookies
        msg = str(record.getMessage())
        if "Bearer " in msg or "checkpoint_auth=" in msg or "code=" in msg:
            record.msg = "[redacted log line]"
            record.args = ()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_sc", False) for h in root.handlers):
        return
    fh = logging.handlers.RotatingFileHandler(paths().logs / "checkpoint.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh._sc = True  # type: ignore[attr-defined]
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    fh.addFilter(_RedactFilter())
    root.addHandler(fh)
    if sys.stdout is not None:  # pythonw has no console
        sh = logging.StreamHandler(sys.stdout)
        sh._sc = True  # type: ignore[attr-defined]
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        sh.addFilter(_RedactFilter())
        root.addHandler(sh)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "uvicorn.access", "faster_whisper", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class AppCore:
    def __init__(self) -> None:
        from .capture.auto import AutoCaptureWorker
        from .extraction.worker import ExtractionWorker
        from .services.capture import CaptureService
        from .services.sessions import SessionManager
        from .sharing.client import SyncWorker
        from .transcription.worker import TranscriptionWorker

        setup_logging()
        migrate()
        self.sessions = SessionManager()
        self.capture = CaptureService(self.sessions)
        self.transcriber = TranscriptionWorker()
        self.organiser = ExtractionWorker()
        self.sync = SyncWorker()
        self.autocapture = AutoCaptureWorker(self.sessions, self.capture)
        self.sessions.set_chunk_callback(lambda _cid: self.transcriber.wake())
        self.supervisor = None
        self.started = False

    def start(self, workers: bool = True) -> None:
        from .extraction.worker import recover_runs
        from .services.sessions import stale_recorder_check
        from .transcription.worker import recover_jobs
        from .workers import Supervisor

        self.sessions.recover_on_launch()
        n = recover_jobs()
        if n:
            notices.push("info", f"Requeued {n} transcription job(s) that were interrupted.", "recovery")
        recover_runs()
        if workers:
            for w in (self.transcriber, self.organiser, self.sync, self.autocapture):
                w.start()
            self.supervisor = Supervisor([self.transcriber, self.organiser, self.sync, self.autocapture], [lambda: stale_recorder_check(self.sessions)])
            self.supervisor.start()
        self.started = True
        log.info("Checkpoint core started; data in %s", paths().root)

    def shutdown(self) -> None:
        log.info("Shutting down")
        try:
            self.sessions.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("Error ending session on shutdown")
        if self.supervisor:
            self.supervisor.stop()
        for w in (self.transcriber, self.organiser, self.sync, self.autocapture):
            w.stop(timeout=3)
        self.autocapture.frames.stop()

    def worker_status(self) -> dict:
        from .settings_store import get_settings
        from .transcription.engine import model_installed
        from .transcription.worker import stalled_message

        st = get_settings()
        return {
            "transcription": {
                "alive": self.transcriber.alive, "busy": self.transcriber.busy, "paused": st.transcription.paused,
                "model": st.transcription.model, "model_installed": model_installed(st.transcription.model),
                "error": stalled_message(self.transcriber) or self.transcriber.model_error or self.transcriber.last_error,
                "stalled": stalled_message(self.transcriber) is not None,
                "device": self.transcriber.engine.device,
                "gpu_fallback": self.transcriber.engine.fallback_message,
            },
            "organiser": {"alive": self.organiser.alive, "busy": self.organiser.busy, "current_run": self.organiser.current_run,
                          "enabled": st.ai.enabled, "error": self.organiser.last_error},
            "auto_capture": self.autocapture.status(),
            "sync": {"alive": self.sync.alive, "busy": self.sync.busy, "offline_reason": self.sync.offline_reason,
                     "last_ok": self.sync.last_ok, "configured": bool(st.sharing.server_url)},
        }


core: Optional[AppCore] = None


def get_core() -> AppCore:
    global core
    if core is None:
        core = AppCore()
    return core
