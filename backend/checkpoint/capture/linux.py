"""Linux screen capture for Wayland sessions.

Wayland doesn't let an app read the screen or other windows directly, so screenshots go
through the xdg-desktop-portal Screenshot API (non-interactive). The portal returns the
whole desktop as one image, which is cropped to the chosen display here. The desktop host
registers the display layout from Qt; without it (headless) the desktop counts as one display.

X11 sessions don't need this module: mss works there as it does on Windows.
"""

from __future__ import annotations

import io
import os
import secrets
import sys
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

IS_LINUX = sys.platform.startswith("linux")
WAYLAND = IS_LINUX and (bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland")

_local = threading.local()
_layout_lock = threading.Lock()
_screens: list[dict] = []  # logical geometry from Qt, set by the desktop host
_desktop_size: Optional[tuple[int, int]] = None  # size of the last portal image


def use_portal() -> bool:
    mode = os.environ.get("CHECKPOINT_SCREENSHOT", "").lower()
    if mode in ("portal", "mss"):
        return mode == "portal"
    return WAYLAND


def set_screens(screens: list[dict]) -> None:
    """[{"name", "left", "top", "width", "height", "primary"}] in Qt logical coordinates."""
    global _screens
    with _layout_lock:
        _screens = [dict(s) for s in screens]


def monitors() -> list[dict]:
    """mss-style monitor dicts (without the "all displays" entry)."""
    with _layout_lock:
        screens = list(_screens)
    if screens:
        return [{"left": s["left"], "top": s["top"], "width": s["width"], "height": s["height"],
                 "is_primary": s["primary"], "unique_id": s["name"] or None} for s in screens]
    if _desktop_size is None:
        try:
            grab_desktop()
        except Exception:  # noqa: BLE001
            return []
    w, h = _desktop_size or (0, 0)
    return [{"left": 0, "top": 0, "width": w, "height": h, "is_primary": True, "unique_id": "desktop"}]


class PortalError(RuntimeError):
    pass


def _connection():  # noqa: ANN202
    conn = getattr(_local, "conn", None)
    if conn is None:
        from jeepney.io.blocking import open_dbus_connection

        conn = open_dbus_connection(bus="SESSION")
        _local.conn = conn
    return conn


def _portal_screenshot() -> Path:
    from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
    from jeepney.io.blocking import Proxy

    conn = _connection()
    portal = DBusAddress("/org/freedesktop/portal/desktop", bus_name="org.freedesktop.portal.Desktop",
                         interface="org.freedesktop.portal.Screenshot")
    token = "checkpoint_" + secrets.token_hex(8)
    sender = conn.unique_name[1:].replace(".", "_")
    rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response",
                     path=f"/org/freedesktop/portal/desktop/request/{sender}/{token}")
    bus = Proxy(message_bus, conn)
    bus.AddMatch(rule)
    try:
        with conn.filter(rule) as queue:
            conn.send_and_get_reply(new_method_call(portal, "Screenshot", "sa{sv}", (
                "", {"handle_token": ("s", token), "interactive": ("b", False)})), timeout=10)
            code, results = conn.recv_until_filtered(queue, timeout=20).body
    finally:
        bus.RemoveMatch(rule)
    if code != 0:
        raise PortalError("The desktop declined the screenshot" if code == 1 else "The screenshot portal failed")
    uri = results.get("uri", ("s", ""))[1]
    if not uri.startswith("file://"):
        raise PortalError(f"The screenshot portal returned an unexpected location: {uri!r}")
    return Path("/" + unquote(uri[len("file://"):]).lstrip("/"))


def grab_desktop():  # noqa: ANN201
    """The whole desktop as an RGB PIL image. The portal's temporary file is removed."""
    global _desktop_size
    from PIL import Image

    try:
        path = _portal_screenshot()
    except PortalError:
        raise
    except Exception as e:  # noqa: BLE001
        _local.conn = None  # reconnect next time
        raise PortalError(f"Couldn't reach the screenshot portal (xdg-desktop-portal): {e}") from e
    try:
        data = path.read_bytes()
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    img = Image.open(io.BytesIO(data)).convert("RGB")
    _desktop_size = img.size
    return img


def grab(region: dict):  # noqa: ANN201
    """Crop the desktop image to an mss-style region given in layout coordinates.

    Qt reports logical coordinates while the portal image is in physical pixels, so the
    region is scaled by the ratio between the image and the layout's bounding box."""
    img = grab_desktop()
    mons = monitors()
    left = min(m["left"] for m in mons)
    top = min(m["top"] for m in mons)
    right = max(m["left"] + m["width"] for m in mons)
    bottom = max(m["top"] + m["height"] for m in mons)
    sx = img.width / max(1, right - left)
    sy = img.height / max(1, bottom - top)
    box = (round((region["left"] - left) * sx), round((region["top"] - top) * sy),
           round((region["left"] - left + region["width"]) * sx), round((region["top"] - top + region["height"]) * sy))
    if box == (0, 0, img.width, img.height):
        return img
    return img.crop(box)
