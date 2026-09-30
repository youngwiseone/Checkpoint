"""Entry point: `python -m checkpoint` launches the desktop host (Windows)."""

import sys
import traceback


def _fatal(message: str) -> None:
    try:
        from .config import paths

        (paths().logs / "startup-error.txt").write_text(message, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if sys.stdout is not None:
        print(message)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message[-1500:], "Checkpoint couldn't start", 0x10)


def run() -> int:
    import os

    # pythonw (used by start.cmd) has no console: give libraries a harmless stream to write to.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # never crash on a character the console can't show
        except (AttributeError, ValueError):
            pass
    if sys.platform != "win32":
        print("The desktop host (tray, hotkeys, capture) targets Windows. Use `python -m checkpoint.headless` elsewhere.")
        return 2
    try:
        from .desktop.host import main

        return main()
    except Exception as e:  # noqa: BLE001
        from .db import DataNewerThanCode

        if isinstance(e, DataNewerThanCode):
            _fatal(str(e))
            return 1
        _fatal("Checkpoint hit an error while starting:\n\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
