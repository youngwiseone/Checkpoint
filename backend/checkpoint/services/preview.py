"""Checks and the live preview for a session's working copy.

"Ready" has to mean the app you're looking at runs the new code. A preview is always started
from the session's working copy, and remembers the commit it was started from; a task only
counts as live when the running preview's commit contains that task's commit and the preview
is still healthy (its URL answers, or its process is still running when there's no URL).
"""

from __future__ import annotations

import functools
import logging
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import paths
from . import workspace as ws

log = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 20 * 60
SETUP_TIMEOUT_S = 20 * 60
START_TIMEOUT_S = 180
NO_URL_SETTLE_S = 4.0


@dataclass
class CommandResult:
    ok: bool
    code: Optional[int]
    output: str
    seconds: float

    def tail(self, lines: int = 25) -> str:
        return "\n".join(self.output.strip().splitlines()[-lines:])


def _popen_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | ws.NO_WINDOW}
    return {"start_new_session": True}


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=ws.NO_WINDOW)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass


def run_command(command: str, cwd: str, timeout: float, log_file: Optional[Path] = None) -> CommandResult:
    """Run a project command (checks or setup) in the working copy through the shell."""
    t0 = time.monotonic()
    proc = subprocess.Popen(command, cwd=cwd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", **_popen_kwargs())
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        out = (proc.communicate()[0] or "") + f"\n[Checkpoint stopped this after {int(timeout // 60)} minutes]"
        return CommandResult(False, None, out, time.monotonic() - t0)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(f"\n$ {command}\n{out}\n[exit {proc.returncode}]\n")
    return CommandResult(proc.returncode == 0, proc.returncode, out or "", time.monotonic() - t0)


def _url_answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - the user's own local preview URL
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


@dataclass
class Preview:
    session_id: str
    project_id: str
    cwd: str
    command: str
    url: str
    commit: str = ""
    state: str = "stopped"  # starting | running | failed | stopped
    error: Optional[str] = None
    started_at: float = 0.0
    proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    log_file: Optional[Path] = None
    _checked: tuple[float, bool] = (0.0, False)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def healthy(self) -> bool:
        if self.state != "running" or not self.alive():
            return False
        if not self.url:
            return True
        at, ok = self._checked
        if time.monotonic() - at > 3:  # the UI polls often; don't hammer the preview
            ok = _url_answers(self.url)
            self._checked = (time.monotonic(), ok)
        return ok


class PreviewManager:
    """At most one preview per project (they usually share a port or a window)."""

    def __init__(self) -> None:
        self._by_project: dict[str, Preview] = {}
        self._lock = threading.RLock()

    def get(self, session_id: str) -> Optional[Preview]:
        with self._lock:
            return next((p for p in self._by_project.values() if p.session_id == session_id), None)

    def start(self, session_id: str, project_id: str, cwd: str, command: str, url: str = "") -> Preview:
        """(Re)start the project's preview from this session's working copy and wait until it serves."""
        with self._lock:
            old = self._by_project.pop(project_id, None)
            if old:
                self._stop(old)
            pv = Preview(session_id=session_id, project_id=project_id, cwd=cwd, command=command, url=url.strip(),
                         log_file=paths().logs / f"preview-{session_id[:8]}.log")
            self._by_project[project_id] = pv
        try:
            pv.commit = ws.head(cwd)
        except ws.GitError as e:
            pv.state, pv.error = "failed", str(e)
            return pv
        pv.log_file.parent.mkdir(parents=True, exist_ok=True)
        out = pv.log_file.open("a", encoding="utf-8")
        out.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {command}  (at {pv.commit[:8]}, in {cwd})\n")
        out.flush()
        pv.state, pv.started_at = "starting", time.time()
        try:
            pv.proc = subprocess.Popen(command, cwd=cwd, shell=True, stdout=out, stderr=subprocess.STDOUT, **_popen_kwargs())
        except OSError as e:
            pv.state, pv.error = "failed", f"Couldn't start the preview: {e}"
            return pv
        deadline = time.monotonic() + START_TIMEOUT_S
        settle = time.monotonic() + NO_URL_SETTLE_S
        while time.monotonic() < deadline:
            if not pv.alive():
                pv.state = "failed"
                pv.error = f"The preview command exited (code {pv.proc.returncode}). See {pv.log_file}."
                return pv
            if pv.url and _url_answers(pv.url):
                break
            if not pv.url and time.monotonic() >= settle:
                break
            time.sleep(0.5)
        else:
            pv.state, pv.error = "failed", f"The preview didn't answer at {pv.url} within {START_TIMEOUT_S // 60} minutes."
            return pv
        pv.state, pv.error = "running", None
        return pv

    def stop(self, session_id: str) -> None:
        with self._lock:
            for pid, pv in list(self._by_project.items()):
                if pv.session_id == session_id:
                    self._by_project.pop(pid)
                    self._stop(pv)

    def _stop(self, pv: Preview) -> None:
        if pv.proc and pv.proc.poll() is None:
            kill_tree(pv.proc.pid)
            try:
                pv.proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
        pv.state = "stopped"

    def stop_all(self) -> None:
        with self._lock:
            for pv in list(self._by_project.values()):
                self._stop(pv)
            self._by_project.clear()

    def status(self, session_id: str) -> dict:
        pv = self.get(session_id)
        if pv is None:
            return {"state": "stopped", "commit": None, "url": None, "error": None}
        state = pv.state
        if state == "running" and not pv.alive():
            state, pv.state = "stopped", "stopped"
            pv.error = "The preview closed."
        return {"state": state, "commit": pv.commit or None, "url": pv.url or None, "error": pv.error,
                "log": str(pv.log_file) if pv.log_file else None}

    def serves(self, session_id: str, commit: Optional[str]) -> bool:
        """True when this session's running preview includes `commit`."""
        pv = self.get(session_id)
        if pv is None or not commit or not pv.commit or not pv.healthy():
            return False
        return pv.commit == commit or _contains(pv.cwd, commit, pv.commit)


@functools.lru_cache(maxsize=512)
def _contains(cwd: str, commit: str, head: str) -> bool:
    """Commits never change, so the answer for a pair never does (the UI asks every second or so)."""
    return ws.is_ancestor(cwd, commit, head)
