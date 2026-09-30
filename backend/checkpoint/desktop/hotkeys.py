"""Global shortcuts via Win32 RegisterHotKey on a dedicated message-loop thread.

Only the configured combinations are registered — no keyboard hook, no keylogging.
Registration conflicts (another app owns the shortcut) are reported per binding.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable, Optional

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_APP = 0x8000
WM_REBIND = WM_APP + 1
WM_QUIT = 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]

NAMED_KEYS = {
    "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "pause": 0x13, "scrolllock": 0x91, "printscreen": 0x2C, "space": 0x20, "backquote": 0xC0, "`": 0xC0,
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63, "numpad4": 0x64, "numpad5": 0x65,
    "numpad6": 0x66, "numpad7": 0x67, "numpad8": 0x68, "numpad9": 0x69,
}
MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}


class HotkeyParseError(ValueError):
    pass


def parse(combo: str) -> tuple[int, int]:
    parts = [p.strip() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise HotkeyParseError("Empty shortcut")
    mods = 0
    for m in parts[:-1]:
        if m.lower() not in MODS:
            raise HotkeyParseError(f"Unknown modifier “{m}”")
        mods |= MODS[m.lower()]
    key = parts[-1].lower()
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
        if mods == 0:
            raise HotkeyParseError("Letter and number keys need a modifier (e.g. Ctrl+Alt+N) so typing isn't intercepted")
    elif key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    else:
        raise HotkeyParseError(f"Unknown key “{parts[-1]}”")
    return mods, vk


class HotkeyThread:
    def __init__(self, bindings_provider: Callable[[], dict[str, str]], on_action: Callable[[str], None]):
        self.bindings_provider = bindings_provider
        self.on_action = on_action
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._ready = threading.Event()
        self._rebound = threading.Event()
        self.status: list[dict] = []
        self._ids: dict[int, str] = {}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkeys")
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(2)

    def rebind(self) -> list[dict]:
        self._rebound.clear()
        user32.PostThreadMessageW(self._tid, WM_REBIND, 0, 0)
        self._rebound.wait(3)
        return self.status

    def summary(self) -> dict:
        failed = [b for b in self.status if not b["registered"]]
        return {"ok": bool(self.status) and not failed, "bindings": self.status,
                "reason": "; ".join(f"{b['keys']}: {b['error']}" for b in failed) or None}

    def _register_all(self) -> None:
        for hid in list(self._ids):
            user32.UnregisterHotKey(None, hid)
        self._ids.clear()
        status = []
        seen: dict[tuple[int, int], str] = {}
        for i, (action, combo) in enumerate(self.bindings_provider().items(), start=1):
            entry = {"action": action, "keys": combo, "registered": False, "error": None}
            try:
                mods, vk = parse(combo)
                if (mods, vk) in seen:
                    raise HotkeyParseError(f"Same shortcut as {seen[(mods, vk)]}")
                seen[(mods, vk)] = action
                if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                    self._ids[i] = action
                    entry["registered"] = True
                else:
                    err = ctypes.get_last_error()
                    entry["error"] = ("Another application is already using this shortcut" if err == ERROR_HOTKEY_ALREADY_REGISTERED
                                      else f"Windows refused the shortcut (error {err})")
            except HotkeyParseError as e:
                entry["error"] = str(e)
            status.append(entry)
            if entry["error"]:
                log.warning("Hotkey %s (%s) not registered: %s", action, combo, entry["error"])
        self.status = status

    def _run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create the message queue
        self._register_all()
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                action = self._ids.get(int(msg.wParam))
                if action:
                    try:
                        self.on_action(action)
                    except Exception:  # noqa: BLE001
                        log.exception("Hotkey action failed")
            elif msg.message == WM_REBIND:
                self._register_all()
                self._rebound.set()
        for hid in list(self._ids):
            user32.UnregisterHotKey(None, hid)
