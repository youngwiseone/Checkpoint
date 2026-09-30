"""In-process notifications for the UI (polled) and desktop-host callbacks.

The desktop host registers callbacks (show quick-note window, toast, etc.). When the
backend runs headless (tests, server-less dev), the callbacks are no-ops and captures
that need a note stay in the "Unfinished captures" inbox.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Notice:
    id: int
    level: str  # info | success | warning | error
    message: str
    source: str = ""
    at: float = field(default_factory=time.time)


class NoticeBoard:
    def __init__(self, maxlen: int = 200):
        self._items: deque[Notice] = deque(maxlen=maxlen)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def push(self, level: str, message: str, source: str = "") -> Notice:
        with self._lock:
            n = Notice(next(self._ids), level, message, source)
            self._items.append(n)
            return n

    def since(self, after_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [n.__dict__.copy() for n in self._items if n.id > after_id]


notices = NoticeBoard()


class DesktopHooks:
    """Set by the desktop host. All callables must be thread-safe (they marshal to Qt)."""

    def __init__(self) -> None:
        self.request_note: Optional[Callable[[dict], None]] = None  # capture needs typed context
        self.request_quick_note: Optional[Callable[[dict], None]] = None  # F9 text-only
        self.toast: Optional[Callable[..., None]] = None  # (level, message, work_rect) non-focus-stealing
        self.state_changed: Optional[Callable[[], None]] = None
        self.note_window_open: Callable[[], bool] = lambda: False
        self.hotkey_status: Callable[[], dict] = lambda: {"available": False, "reason": "Desktop host not running"}
        self.rebind_hotkeys: Optional[Callable[[], dict]] = None
        self.offer_session: Optional[Callable[[dict], None]] = None  # a project's program started: offer a session

    @property
    def desktop_available(self) -> bool:
        return self.request_note is not None

    def emit_toast(self, level: str, message: str, work: Optional[dict] = None) -> None:
        if self.toast:
            try:
                self.toast(level, message, work)
            except Exception:  # noqa: BLE001 - UI failure must never break capture
                pass

    def emit_offer(self, offer: dict) -> None:
        if self.offer_session:
            try:
                self.offer_session(offer)
            except Exception:  # noqa: BLE001
                pass

    def emit_state(self) -> None:
        if self.state_changed:
            try:
                self.state_changed()
            except Exception:  # noqa: BLE001
                pass


hooks = DesktopHooks()
