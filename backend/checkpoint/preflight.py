"""Launch preflight: explains problems instead of letting a window close silently.

Exit code 0 = OK (warnings allowed), 1 = cannot start.
"""

from __future__ import annotations

import importlib
import sys


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    if sys.version_info < (3, 11):
        errors.append(f"Python 3.11+ is required (found {sys.version.split()[0]}). Install Python 3.11 and re-run setup.ps1.")
    for mod, why in (("fastapi", "web server"), ("sqlalchemy", "database"), ("alembic", "database migrations"),
                     ("PySide6.QtWidgets", "tray icon and note window"), ("mss", "screenshots"), ("PIL", "image encoding")):
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Missing {mod} ({why}): {e}. Run setup.ps1 again.")
    for mod, why in (("pyaudiowpatch", "microphone/computer audio recording"), ("faster_whisper", "local transcription"),
                     ("keyring", "saving the shared-workspace token")):
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"{mod} unavailable — {why} will be disabled ({e.__class__.__name__}).")
    try:
        from .config import FRONTEND_DIST, paths

        p = paths()
        test = p.root / ".write-test"
        test.write_text("ok")
        test.unlink()
        if not (FRONTEND_DIST / "index.html").is_file():
            warnings.append("The interface isn't built (frontend/dist missing). Run setup.ps1 to build it.")
    except Exception as e:  # noqa: BLE001
        errors.append(f"The data folder isn't writable: {e}")
    if sys.platform != "win32":
        errors.append("The desktop app targets Windows. Use `python -m checkpoint.headless` on other systems.")
    for w in warnings:
        print(f"  [warning] {w}")
    for e in errors:
        print(f"  [error]   {e}")
    if not errors:
        print("  Preflight OK.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
