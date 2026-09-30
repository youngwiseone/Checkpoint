"""Screenshot grabbing with mss.

Only the configured region is captured: the monitor containing the foreground
window (default), a specific monitor chosen for the session, or the foreground
window rectangle. Never every display by default.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field
from typing import Optional

from . import win32

_local = threading.local()


def _mss():
    import mss

    inst = getattr(_local, "mss", None)
    if inst is None:
        inst = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
        _local.mss = inst
    return inst


def list_monitors() -> list[dict]:
    try:
        mons = _mss().monitors[1:]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for i, m in enumerate(mons, start=1):
        out.append(
            {
                "index": i,
                "id": m.get("unique_id") or f"monitor-{i}",
                "name": f"Display {i}" + (" (primary)" if m.get("is_primary") else ""),
                "left": m["left"],
                "top": m["top"],
                "width": m["width"],
                "height": m["height"],
                "primary": bool(m.get("is_primary")),
            }
        )
    return out


@dataclass
class Grab:
    png: bytes
    thumb_jpeg: bytes
    width: int
    height: int
    monitor: dict
    window_title: str
    fg_hwnd: int
    work_rect: Optional[dict]
    looks_blank: bool
    warnings: list[str] = field(default_factory=list)


def _monitor_for_rect(rect: win32.Rect | None) -> Optional[dict]:
    if rect is None:
        return None
    for m in list_monitors():
        if m["left"] == rect.left and m["top"] == rect.top:
            return m
    # Fallback: monitor containing the rect's centre.
    cx, cy = rect.left + rect.width // 2, rect.top + rect.height // 2
    for m in list_monitors():
        if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
            return m
    return None


def resolve_region(target: str, region_mode: str, fg: Optional[win32.ForegroundInfo]) -> tuple[dict, dict]:
    """Returns (mss region, monitor descriptor)."""
    mons = list_monitors()
    if not mons:
        raise RuntimeError("No displays were detected for screenshot capture")
    monitor = None
    if target and target != "foreground":
        monitor = next((m for m in mons if m["id"] == target or str(m["index"]) == target), None)
    if monitor is None and fg is not None:
        monitor = _monitor_for_rect(fg.monitor_rect)
    if monitor is None:
        monitor = next((m for m in mons if m["primary"]), mons[0])
    region = {k: monitor[k] for k in ("left", "top", "width", "height")}
    if region_mode == "foreground_window" and fg is not None and fg.window_rect is not None and (not target or target == "foreground"):
        wr = fg.window_rect
        # Clip the window to the virtual desktop bounds of its monitor.
        l = max(wr.left, monitor["left"])
        t = max(wr.top, monitor["top"])
        r = min(wr.left + wr.width, monitor["left"] + monitor["width"])
        b = min(wr.top + wr.height, monitor["top"] + monitor["height"])
        if r - l > 50 and b - t > 50:
            region = {"left": l, "top": t, "width": r - l, "height": b - t}
    return region, monitor


def _grab_image(target: str, region_mode: str):  # noqa: ANN202
    from PIL import Image

    fg = win32.foreground_info()
    region, monitor = resolve_region(target, region_mode, fg)
    shot = _mss().grab(region)
    return Image.frombytes("RGB", shot.size, shot.rgb), fg, region, monitor


def _to_grab(img, fg, region: dict, monitor: dict) -> Grab:  # noqa: ANN001
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    thumb = img.copy()
    thumb.thumbnail((480, 480))
    tbuf = io.BytesIO()
    thumb.save(tbuf, format="JPEG", quality=80)
    # Detect an all-black/blank grab (exclusive fullscreen or protected content).
    small = img.resize((32, 18)).convert("L")
    extrema = small.getextrema()
    looks_blank = extrema[1] < 8
    warnings = []
    if looks_blank:
        warnings.append(
            "The screenshot looks blank. Exclusive-fullscreen or protected content can't be captured — "
            "switch the app to borderless/windowed mode."
        )
    work = None
    if fg and fg.work_rect and _monitor_for_rect(fg.monitor_rect) and _monitor_for_rect(fg.monitor_rect)["id"] == monitor["id"]:
        work = fg.work_rect.as_mss()
    return Grab(
        png=buf.getvalue(),
        thumb_jpeg=tbuf.getvalue(),
        width=img.width,
        height=img.height,
        monitor={**monitor, "region": region},
        window_title=(fg.title if fg else "")[:500],
        fg_hwnd=fg.hwnd if fg else 0,
        work_rect=work or {k: monitor[k] for k in ("left", "top", "width", "height")},
        looks_blank=looks_blank,
        warnings=warnings,
    )


def grab(target: str = "foreground", region_mode: str = "foreground_monitor") -> Grab:
    return _to_grab(*_grab_image(target, region_mode))


@dataclass
class Frame:
    """A cheap in-memory grab for the auto-capture buffer: JPEG only, turned into a full Grab if kept."""

    mono: float
    jpeg: bytes
    fg: Optional[win32.ForegroundInfo]
    region: dict
    monitor: dict


def grab_frame(mono: float, target: str = "foreground", region_mode: str = "foreground_monitor") -> Frame:
    img, fg, region, monitor = _grab_image(target, region_mode)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return Frame(mono=mono, jpeg=buf.getvalue(), fg=fg, region=region, monitor=monitor)


def frame_to_grab(f: Frame) -> Grab:
    from PIL import Image

    return _to_grab(Image.open(io.BytesIO(f.jpeg)).convert("RGB"), f.fg, f.region, f.monitor)


def foreground_monitor_id() -> Optional[str]:
    fg = win32.foreground_info()
    m = _monitor_for_rect(fg.monitor_rect) if fg else None
    return m["id"] if m else None
