"""Small ctypes wrappers for Windows window/monitor information.

Imported lazily; on non-Windows platforms the functions degrade to None/empty.
The process is expected to be per-monitor DPI aware (Qt 6 sets this), so all
coordinates are physical pixels matching mss.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

MONITOR_DEFAULTTONEAREST = 2
WDA_EXCLUDEFROMCAPTURE = 0x11
DWMWA_EXTENDED_FRAME_BOUNDS = 9


@dataclass
class Rect:
    left: int
    top: int
    width: int
    height: int

    def as_mss(self) -> dict:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


@dataclass
class ForegroundInfo:
    hwnd: int
    title: str
    window_rect: Optional[Rect]
    monitor_rect: Optional[Rect]
    work_rect: Optional[Rect]
    monitor_device: str
    pid: int


def foreground_info() -> Optional[ForegroundInfo]:
    if not IS_WIN:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    win_rect = window_rect(hwnd)
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    mon_rect = work = None
    device = ""
    if mon:
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            r = mi.rcMonitor
            w = mi.rcWork
            mon_rect = Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
            work = Rect(w.left, w.top, w.right - w.left, w.bottom - w.top)
            device = mi.szDevice
    return ForegroundInfo(int(hwnd), buf.value, win_rect, mon_rect, work, device, int(pid.value))


def window_rect(hwnd: int) -> Optional[Rect]:
    if not IS_WIN:
        return None
    r = RECT()
    # Extended frame bounds exclude the invisible resize borders on Windows 10/11.
    if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    return Rect(r.left, r.top, w, h)


def restore_foreground(hwnd: int) -> bool:
    if not IS_WIN or not hwnd:
        return False
    try:
        if not user32.IsWindow(hwnd):
            return False
        return bool(user32.SetForegroundWindow(hwnd))
    except OSError:
        return False


def exclude_from_capture(hwnd: int) -> bool:
    """Hide our own popups from screen capture (Windows 10 2004+). Best effort."""
    if not IS_WIN or not hwnd:
        return False
    try:
        return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except (OSError, AttributeError):
        return False


def current_pid() -> int:
    import os

    return os.getpid()
