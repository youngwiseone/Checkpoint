"""Desktop notifications (org.freedesktop.Notifications) for toasts on Wayland.

Wayland doesn't let an app place a borderless always-on-top popup where it wants, but the
notification daemon shows messages without taking focus, which is what the toast is for.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)

URGENCY = {"error": 2, "warning": 1}


class Notifier:
    def __init__(self) -> None:
        self._conn = None
        self._last_id = 0
        self._lock = threading.Lock()

    def show(self, level: str, message: str, timeout_ms: int) -> bool:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        addr = DBusAddress("/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications",
                           interface="org.freedesktop.Notifications")
        hints = {"urgency": ("y", URGENCY.get(level, 0)), "transient": ("b", level != "error")}
        with self._lock:
            try:
                if self._conn is None:
                    self._conn = open_dbus_connection(bus="SESSION")
                # Replace the previous toast instead of stacking them.
                msg = new_method_call(addr, "Notify", "susssasa{sv}i",
                                      ("Checkpoint", self._last_id, "", "Checkpoint", message, [], hints, timeout_ms))
                self._last_id = self._conn.send_and_get_reply(msg, timeout=2).body[0]
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("Desktop notification failed: %s", e)
                self._conn = None
                return False


_notifier: Optional[Notifier] = None


def notify(level: str, message: str, timeout_ms: int) -> bool:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier.show(level, message, timeout_ms)
