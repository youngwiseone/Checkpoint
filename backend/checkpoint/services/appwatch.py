"""Offer to start a session when a project's program is running.

A project can name the program it's tested with (its exe, or part of its window title).
Every few seconds the visible top-level windows are listed (exe names and titles only);
when a watched program appears and no session is running, the desktop host offers a
session for that project, started with the start-session shortcut or by clicking the
tray message. Nothing is recorded until the user accepts.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from ..db import read_session
from ..events import hooks
from ..models import Project
from ..settings_store import WatchRule, get_settings
from ..workers import Worker

log = logging.getLogger(__name__)

OFFER_TTL_S = 15 * 60


def _norm_exe(name: str) -> str:
    n = name.strip().lower()
    return n[:-4] if n.endswith(".exe") else n


def rule_matches(rule: WatchRule, windows: list[dict]) -> Optional[dict]:
    """The first window belonging to the rule's program, if it is running."""
    want = rule.match.strip().lower()
    if not want:
        return None
    for w in windows:
        if rule.kind == "exe" and _norm_exe(w.get("exe", "")) == _norm_exe(want):
            return w
        if rule.kind == "title" and want in (w.get("title") or "").lower():
            return w
    return None


class AppWatcher(Worker):
    name = "app-watch"
    idle_sleep = 3.0

    def __init__(self, sessions, list_windows: Optional[Callable[[], list[dict]]] = None) -> None:  # noqa: ANN001
        super().__init__()
        self.sessions = sessions
        if list_windows is None:
            from ..capture.win32 import visible_windows

            list_windows = visible_windows
        self.list_windows = list_windows
        self.running: set[str] = set()  # project ids whose program is running
        self.offer: Optional[dict] = None

    def step(self) -> bool:
        st = get_settings().app_watch
        if not st.enabled or not st.rules:
            self.running.clear()
            self.offer = None
            return False
        try:
            windows = self.list_windows()
        except Exception as e:  # noqa: BLE001 - try again next tick
            self.last_error = f"Couldn't list running programs: {e}"
            return False
        now_running: dict[str, dict] = {}
        for rule in st.rules:
            w = rule_matches(rule, windows)
            if w and rule.project_id not in now_running:
                now_running[rule.project_id] = w
        started = [pid for pid in now_running if pid not in self.running]
        self.running = set(now_running)
        if self.offer and (self.offer["project_id"] not in self.running or time.time() - self.offer["at"] > OFFER_TTL_S):
            self.offer = None
        if self.sessions.active is not None:
            self.offer = None
            return False
        for pid in started:
            with read_session() as s:
                p = s.get(Project, pid)
                if p is None or p.archived or p.is_demo:
                    continue
                name = p.name
            w = now_running[pid]
            self.offer = {"project_id": pid, "project_name": name, "program": w.get("title") or w.get("exe"), "at": time.time()}
            hooks.emit_offer(self.offer)
            break
        return False

    def take_offer(self) -> Optional[dict]:
        o, self.offer = self.offer, None
        return o

    def dismiss(self) -> None:
        self.offer = None

    def status(self) -> dict:
        return {"alive": self.alive, "offer": self.offer, "running": sorted(self.running), "error": self.last_error}
