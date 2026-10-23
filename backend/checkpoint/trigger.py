"""Send a shortcut action to the running Checkpoint desktop host (Linux).

On Wayland an app can't register global shortcuts itself, so the desktop environment owns
them: bind a custom shortcut (e.g. F8) to

    <python> /path/to/backend/checkpoint/trigger.py capture

Actions (a desktop shortcut can't report how long a key is held, so each gesture of the
Windows capture key gets its own command):
    capture  tap: screenshot; press twice quickly to edit the last task
    review   hold: open the menu / review inbox
    voice    tap then hold: first press starts a voice note, second press saves it
Standard library only, so it starts fast and runs with any Python 3.
"""

from __future__ import annotations

import getpass
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ACTIONS = ("capture", "review", "voice")


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and Path(runtime).is_dir():
        return Path(runtime) / "checkpoint.sock"
    return Path(f"/tmp/checkpoint-{getpass.getuser()}.sock")


def command(action: str) -> str:
    """The shell command a desktop shortcut should run for `action`."""
    return f"{sys.executable} {Path(__file__).resolve()} {action}"


def send(action: str, timeout: float = 2.0) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(socket_path()))
        s.sendall(action.encode() + b"\n")
        return s.recv(256).decode(errors="replace").strip()


def _tell(message: str) -> None:
    print(message, file=sys.stderr)
    if not sys.stderr.isatty() and shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Checkpoint", "Checkpoint", message], check=False)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ACTIONS:
        print(f"Usage: {Path(__file__).name} {{{','.join(ACTIONS)}}}", file=sys.stderr)
        return 2
    try:
        reply = send(argv[0])
    except OSError:
        _tell("Checkpoint isn't running. Start it with ./start.sh first.")
        return 1
    if reply != "ok":
        _tell(f"Checkpoint didn't accept the shortcut: {reply or 'no reply'}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
