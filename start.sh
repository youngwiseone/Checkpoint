#!/bin/sh
# Checkpoint launcher for Linux. Run ./setup.sh once first.
#   ./start.sh            start in the background (tray icon shows the state)
#   ./start.sh --console  run attached to this terminal for troubleshooting
set -u
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8 HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_PROGRESS_BARS=1
PY="$(pwd)/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo ""
    echo " Checkpoint isn't set up yet. Run this once from this folder:"
    echo "     ./setup.sh"
    echo ""
    exit 1
fi

cd backend
echo "Checking prerequisites..."
if ! "$PY" -m checkpoint.preflight; then
    echo ""
    echo " Checkpoint can't start. Fix the errors above, then run ./start.sh again."
    exit 1
fi

if [ "${1:-}" = "--console" ]; then
    echo "Running attached to this terminal. Press Ctrl+C or use the tray icon > Quit to stop."
    exec "$PY" -m checkpoint
fi

# Normal use: detach from the terminal. Errors are shown as a notification and in the log folder.
if command -v setsid >/dev/null; then
    setsid "$PY" -m checkpoint </dev/null >/dev/null 2>&1 &
else
    nohup "$PY" -m checkpoint </dev/null >/dev/null 2>&1 &
fi
echo "Checkpoint is starting - look for its icon in the system tray."
echo "(Logs: ${CHECKPOINT_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/Checkpoint}/logs)"
