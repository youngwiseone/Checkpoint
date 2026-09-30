"""FastAPI application for the local UI (served on 127.0.0.1 only)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..config import DEV_MODE, FRONTEND_DIST, HOST
from .routes import router
from .security import AuthState, LocalSecurityMiddleware

log = logging.getLogger(__name__)

MISSING_UI = """<!doctype html><meta charset=utf-8><title>Checkpoint</title>
<body style="font:16px system-ui;background:#15171c;color:#e8e9ec;padding:48px">
<h1>Checkpoint</h1><p>The interface hasn't been built yet. Run <code>setup.ps1</code> (or <code>npm run build</code> in <code>frontend/</code>) and restart.</p></body>"""


def create_app(auth: AuthState) -> FastAPI:
    app = FastAPI(title="Checkpoint", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth = auth
    app.add_middleware(LocalSecurityMiddleware, auth=auth)
    app.include_router(router)

    @app.exception_handler(Exception)
    async def unhandled(_req, exc: Exception):  # noqa: ANN001, ANN202
        log.exception("Unhandled API error")
        return JSONResponse({"detail": f"Unexpected error: {exc.__class__.__name__}. Details are in the log file."}, status_code=500)

    dist = FRONTEND_DIST

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):  # noqa: ANN202
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if not (dist / "index.html").is_file():
            return HTMLResponse(MISSING_UI)
        target = (dist / full_path).resolve()
        if full_path and target.is_file() and dist.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


class ServerThread:
    """Runs uvicorn in a background thread so the Qt main thread stays free."""

    def __init__(self, app: FastAPI, port: int):
        import uvicorn

        # log_config=None: the app configures logging itself (uvicorn's default needs a console).
        self.config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False, lifespan="off", log_config=None)
        self.server = uvicorn.Server(self.config)
        self.server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="api-server")
        self.thread.start()

    def wait_started(self, timeout: float = 10) -> bool:
        import time

        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.server.started:
                return True
            if self.thread and not self.thread.is_alive():
                return False
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        self.server.should_exit = True
        if self.thread:
            self.thread.join(5)


def pick_port(preferred: int) -> int:
    import socket

    for port in [preferred] + list(range(preferred + 1, preferred + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found")


def dev_origins() -> list[str]:
    return ["http://127.0.0.1:5173", "http://localhost:5173"] if DEV_MODE else []
