"""Run the backend without the desktop host (no tray, hotkeys or native windows).

Useful for UI development: `python -m checkpoint.headless`. Captures that need
a note go to the Unfinished captures inbox because no native window exists.
"""

from __future__ import annotations

import signal
import sys
import threading
import webbrowser

from .api.app import ServerThread, create_app, dev_origins, pick_port
from .api.security import AuthState
from .config import DEFAULT_PORT
from .core import get_core


def main() -> None:
    core = get_core()
    core.start()
    port = pick_port(DEFAULT_PORT)
    auth = AuthState(port, dev_origins())
    server = ServerThread(create_app(auth), port)
    server.start()
    if not server.wait_started():
        raise SystemExit("The local server failed to start")
    url = auth.open_url()
    print(f"Checkpoint (headless) running on http://127.0.0.1:{port}")
    print("Open this one-time link to sign the browser in (valid for 2 minutes):")
    print(url)
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.wait(0.5):
        pass
    server.stop()
    core.shutdown()


if __name__ == "__main__":
    main()
