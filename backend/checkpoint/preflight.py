"""Launch preflight: explains problems instead of letting a window close silently.

Exit code 0 = OK (warnings allowed), 1 = cannot start.
"""

from __future__ import annotations

import importlib
import sys


def main() -> int:
    linux = sys.platform.startswith("linux")
    setup = "setup.sh" if linux else "setup.ps1"
    errors: list[str] = []
    warnings: list[str] = []
    if sys.version_info < (3, 11):
        errors.append(f"Python 3.11+ is required (found {sys.version.split()[0]}). Install Python 3.11 and re-run {setup}.")
    for mod, why in (("sqlalchemy", "database"), ("alembic", "database migrations"),
                     ("PySide6.QtWidgets", "tray icon and note window"), ("mss", "screenshots"), ("PIL", "image encoding")):
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Missing {mod} ({why}): {e}. Run {setup} again.")
    optional = [("needle", "Whistle local transcription"), ("keyring", "saving the shared-workspace token")]
    if linux:
        optional += [("checkpoint.audio.pulse", "microphone/computer audio recording")]
        from .capture import linux as capture_linux

        if capture_linux.use_portal():
            optional += [("jeepney", "screenshots through the desktop portal")]
    else:
        optional += [("pyaudiowpatch", "microphone/computer audio recording")]
    for mod, why in optional:
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"{mod} unavailable — {why} will be disabled ({e.__class__.__name__}: {e}).")
    try:
        from .config import paths

        p = paths()
        test = p.root / ".write-test"
        test.write_text("ok")
        test.unlink()
    except Exception as e:  # noqa: BLE001
        errors.append(f"The data folder isn't writable: {e}")
    if sys.platform != "win32" and not linux:
        errors.append("The desktop app targets Windows and Linux. Use `python -m checkpoint.headless` on other systems.")
    for w in warnings:
        print(f"  [warning] {w}")
    for e in errors:
        print(f"  [error]   {e}")
    if not errors:
        print("  Preflight OK.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
