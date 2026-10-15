"""Global shortcuts on Linux: the desktop environment runs `trigger.py <action>`.

Wayland gives apps no way to grab keys globally, so the shortcuts are bound in the desktop's
own settings (COSMIC, GNOME, KDE, Sway, ...). Each press runs the trigger script, which
sends the action over a per-user Unix socket to this listener. Same interface as the
Windows HotkeyThread. A trigger only reports a press, never how long the key is held, so
each gesture of the capture key is its own action and is replayed as the Windows key events.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Callable, Optional

from .. import trigger

log = logging.getLogger(__name__)


class ShortcutListener:
    def __init__(self, bindings_provider: Callable[[], dict[str, str]], on_action: Callable[[str], None]):
        self.bindings_provider = bindings_provider
        self.on_action = on_action
        self.path = trigger.socket_path()
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None
        self._voice = False  # a `voice` trigger started dictation; the next one stops it

    def start(self) -> None:
        try:
            self.path.unlink(missing_ok=True)  # left over from a crash; single-instance is checked earlier
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(self.path))
            os.chmod(self.path, 0o600)
            sock.listen(8)
        except OSError as e:
            self.error = f"Couldn't listen for shortcuts on {self.path}: {e}"
            log.warning(self.error)
            return
        self._sock = sock
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkeys")
        self._thread.start()

    def stop(self) -> None:
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None
            self.path.unlink(missing_ok=True)
        if self._thread:
            self._thread.join(2)

    def rebind(self) -> list[dict]:
        return self.summary()["bindings"]

    def summary(self) -> dict:
        keys = self.bindings_provider()
        bindings = [{"action": a, "keys": keys.get(a, ""), "registered": self._sock is not None, "external": True,
                     "command": trigger.command(a), "error": self.error}
                    for a in trigger.ACTIONS]
        return {"ok": self._sock is not None, "external": True, "bindings": bindings, "reason": self.error}

    def _run(self) -> None:
        # Actions run one at a time on this thread, like WM_HOTKEY messages on Windows.
        while self._sock is not None:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(1)
                    action = conn.recv(64).decode(errors="replace").strip()
                    ok = action in trigger.ACTIONS
                    conn.sendall(b"ok\n" if ok else b"unknown action\n")
                except OSError:
                    continue
            if ok:
                try:
                    for event in self._events(action):
                        self.on_action(event)
                except Exception:  # noqa: BLE001
                    log.exception("Shortcut action failed")

    def _events(self, action: str) -> list[str]:
        """The key events the Windows capture key would send for this gesture."""
        events = []
        if self._voice:  # the next trigger ends a running voice note, like releasing the key
            self._voice = False
            events.append("release")
            if action == "voice":
                return events
        if action == "capture":
            return events + ["press", "capture"]
        if action == "review":
            return events + ["review"]
        self._voice = True
        return events + ["press", "capture", "press", "hold"]
