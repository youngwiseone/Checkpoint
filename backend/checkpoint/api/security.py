"""Local API protection.

- The server binds to 127.0.0.1 only.
- A per-launch secret lives in an HttpOnly, SameSite=Strict cookie. The browser gets
  it through a single-use, short-lived code that the tray/launcher opens; the secret
  itself never appears in a URL, log or localStorage.
- Host header validation blocks DNS-rebinding; Origin/Sec-Fetch-Site checks plus a
  required custom header block cross-site requests from arbitrary websites.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

COOKIE = "checkpoint_auth"
CODE_TTL = 120


class AuthState:
    def __init__(self, port: int, extra_origins: Iterable[str] = ()):  # noqa: B006
        self.secret = secrets.token_urlsafe(32)
        self.port = port
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()
        self.extra_origins = set(extra_origins)

    @property
    def allowed_hosts(self) -> set[str]:
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        for o in self.extra_origins:
            hosts.add(o.split("://", 1)[-1])
        return hosts

    @property
    def allowed_origins(self) -> set[str]:
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"} | self.extra_origins

    def new_code(self) -> str:
        code = secrets.token_urlsafe(24)
        with self._lock:
            now = time.monotonic()
            self._codes = {c: t for c, t in self._codes.items() if t > now}
            self._codes[code] = now + CODE_TTL
        return code

    def open_url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}/auth/open?code={self.new_code()}&next={path}"

    def redeem(self, code: str) -> bool:
        with self._lock:
            exp = self._codes.pop(code, None)
        return exp is not None and exp > time.monotonic()

    def cookie_ok(self, value: str | None) -> bool:
        return bool(value) and hmac.compare_digest(value, self.secret)


PUBLIC_PATHS = {"/api/auth/status", "/api/health"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth: AuthState):  # noqa: ANN001
        super().__init__(app)
        self.auth = auth

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        host = request.headers.get("host", "")
        if host not in self.auth.allowed_hosts:
            return PlainTextResponse("Invalid host", status_code=421)
        path = request.url.path
        site = request.headers.get("sec-fetch-site")
        if path == "/auth/open":
            code = request.query_params.get("code", "")
            nxt = request.query_params.get("next", "/")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = "/"
            if not self.auth.redeem(code):
                return PlainTextResponse("This link has expired. Use “Open Checkpoint” from the tray icon.", status_code=403)
            resp = RedirectResponse(nxt, status_code=303)
            resp.set_cookie(COOKIE, self.auth.secret, httponly=True, samesite="strict", path="/")
            resp.headers["Referrer-Policy"] = "no-referrer"
            return resp
        if path.startswith("/api/"):
            if site == "cross-site":
                return JSONResponse({"detail": "Cross-site requests are not allowed"}, status_code=403)
            if request.method in MUTATING:
                origin = request.headers.get("origin")
                if origin is not None and origin not in self.auth.allowed_origins:
                    return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
                if request.headers.get("x-checkpoint-request") != "1":
                    return JSONResponse({"detail": "Missing request header"}, status_code=403)
            if path not in PUBLIC_PATHS and not self.auth.cookie_ok(request.cookies.get(COOKIE)):
                return JSONResponse({"detail": "Not signed in to this app instance. Use “Open” from the Checkpoint tray icon."}, status_code=401)
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not path.startswith("/api/media") and not path.startswith("/api/segments"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
        return response
