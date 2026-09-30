"""Ollama provider (local HTTP API only). No cloud fallback exists in this app."""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional, Type

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Actionable failure message suitable for the UI."""


# ------------------------------------------------------------------ auto-start
_start_lock = threading.Lock()
_started_at = 0.0


def _is_local(base_url: str) -> bool:
    from urllib.parse import urlparse

    return (urlparse(base_url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def find_ollama_exe() -> Optional[str]:
    import os
    import shutil
    from pathlib import Path

    exe = shutil.which("ollama")
    if exe:
        return exe
    candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"]
    user_path = _user_env("Path") or ""
    candidates += [Path(p) / "ollama.exe" for p in user_path.split(";") if p]
    return next((str(c) for c in candidates if c.is_file()), None)


def _user_env(name: str) -> Optional[str]:
    """Read a user environment variable from the registry (set after this process started)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except (ImportError, OSError):
        return None


def _reachable(base_url: str) -> bool:
    try:
        return httpx.get(f"{base_url.rstrip('/')}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_running(base_url: str, wait_s: float = 20) -> bool:
    """Start a local `ollama serve` in the background if it isn't running. Returns reachability.

    Only for a loopback address, and only when AI features actually need it.
    """
    import os
    import subprocess
    import sys
    import time

    global _started_at
    if _reachable(base_url):
        return True
    if not _is_local(base_url):
        return False
    with _start_lock:
        if _reachable(base_url):
            return True
        if time.monotonic() - _started_at > wait_s:
            exe = find_ollama_exe()
            if not exe:
                return False
            env = dict(os.environ)
            models = env.get("OLLAMA_MODELS") or _user_env("OLLAMA_MODELS")
            if models:
                env["OLLAMA_MODELS"] = models
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            log.info("Starting Ollama in the background: %s serve", exe)
            subprocess.Popen([exe, "serve"], env=env, creationflags=flags, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
            _started_at = time.monotonic()
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if _reachable(base_url):
                return True
            time.sleep(0.5)
    return False


class OllamaProvider:
    def __init__(self, base_url: str, timeout: float = 240, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.Client] = None
        self._lock = threading.Lock()
        self._think_supported: dict[str, bool] = {}

    def _c(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(self.timeout, connect=5))
            return self._client

    def close(self) -> None:
        """Also used to abort an in-flight request when a run is cancelled."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None

    # ------------------------------------------------------------------ status
    def version(self) -> Optional[str]:
        try:
            r = httpx.get(f"{self.base_url}/api/version", timeout=3)
            r.raise_for_status()
            return r.json().get("version")
        except Exception:  # noqa: BLE001
            return None

    def list_models(self) -> list[dict]:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"Ollama isn't reachable at {self.base_url}. Install Ollama and make sure it is running. ({e.__class__.__name__})") from e
        return [{"name": m.get("name"), "size": m.get("size"), "family": (m.get("details") or {}).get("family"),
                 "parameters": (m.get("details") or {}).get("parameter_size")} for m in r.json().get("models", [])]

    # ------------------------------------------------------------------ chat
    def chat_json(self, model: str, system: str, user: str, schema: Type[BaseModel], num_ctx: int = 8192) -> BaseModel:
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            body = {
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "format": schema.model_json_schema(),
                "stream": False,
                "options": {"temperature": 0, "num_ctx": num_ctx},
            }
            if self._think_supported.get(model, True):
                body["think"] = False
            try:
                r = self._c().post("/api/chat", json=body)
            except httpx.TimeoutException as e:
                last_err = ProviderError(f"The local model timed out after {self.timeout:.0f}s. Try a smaller model or increase the timeout.")
                continue
            except (httpx.TransportError, RuntimeError) as e:
                raise ProviderError(f"Couldn't reach Ollama at {self.base_url}: {e.__class__.__name__}") from e
            if r.status_code == 404:
                raise ProviderError(f"Model “{model}” isn't installed in Ollama. Pull it from Settings → Local AI, or run: ollama pull {model}")
            if r.status_code >= 400:
                text = r.text[:300]
                if "think" in text.lower() and body.get("think") is not None:
                    self._think_supported[model] = False
                    continue
                last_err = ProviderError(f"Ollama returned an error ({r.status_code}): {text}")
                continue
            try:
                content = r.json()["message"]["content"]
                data = json.loads(content)
                return schema.model_validate(data)
            except (KeyError, ValueError, ValidationError) as e:
                last_err = ProviderError(f"The model returned output that didn't match the expected structure (attempt {attempt + 1}).")
                log.warning("Invalid model output: %s", e)
                continue
        raise last_err or ProviderError("The model request failed")


class PullManager:
    """Explicit `ollama pull` with progress, started only by the user."""

    def __init__(self) -> None:
        self.state: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, base_url: str, model: str) -> dict:
        with self._lock:
            cur = self.state.get(model)
            if cur and cur.get("state") == "pulling":
                return cur
            self.state[model] = {"state": "pulling", "status": "starting", "completed": 0, "total": 0, "error": None}
        threading.Thread(target=self._run, args=(base_url, model), daemon=True, name="ollama-pull").start()
        return self.state[model]

    def _run(self, base_url: str, model: str) -> None:
        st = self.state[model]
        try:
            st["status"] = "starting Ollama"
            ensure_running(base_url)
            with httpx.stream("POST", f"{base_url.rstrip('/')}/api/pull", json={"model": model, "stream": True},
                              timeout=httpx.Timeout(None, connect=5)) as r:
                if r.status_code >= 400:
                    raise ProviderError(f"Pull failed ({r.status_code}): {r.read()[:200]!r}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    if "error" in msg:
                        raise ProviderError(msg["error"])
                    st["status"] = msg.get("status", "")
                    if msg.get("total"):
                        st["total"] = msg["total"]
                        st["completed"] = msg.get("completed", 0)
            st["state"] = "done"
        except httpx.TransportError as e:
            st.update(state="failed", error=f"Couldn't reach Ollama at {base_url}. Is it running? ({e.__class__.__name__})")
        except Exception as e:  # noqa: BLE001
            st.update(state="failed", error=str(e)[:300])


pulls = PullManager()
