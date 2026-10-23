#!/bin/sh
# Checkpoint - one-time setup for Linux (no root needed unless a distro package is missing).
# Usage (from this folder):  ./setup.sh
# Creates .venv, installs pinned Python packages, builds the interface.
# It never downloads speech or AI models - that's an explicit action in Settings.
set -eu
cd "$(dirname "$0")"
ROOT=$(pwd)

fail() { printf '\n  \033[31mSetup stopped:\033[0m %s\n\n' "$1"; exit 1; }
step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
warn() { printf '    \033[33m%s\033[0m\n' "$1"; }

# The distro's Qt bindings are built for the system Python, so prefer it.
qt_hint() {
    if command -v xbps-install >/dev/null; then echo "sudo xbps-install -S python3-pyside6-gui python3-pyside6-widgets python3-pyside6-network"
    elif command -v apt-get >/dev/null; then echo "sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork"
    elif command -v pacman >/dev/null; then echo "sudo pacman -S pyside6"
    elif command -v dnf >/dev/null; then echo "sudo dnf install python3-pyside6"
    elif command -v zypper >/dev/null; then echo "sudo zypper install python3-pyside6"
    else echo "install PySide6 (Qt 6 for Python) from your distribution"; fi
}

# ---------------------------------------------------------------- Python 3.11+
step "Checking Python"
PYTHON=""
for c in python3 python3.13 python3.12 python3.11; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        PYTHON=$(command -v "$c"); break
    fi
done
[ -n "$PYTHON" ] || fail "Python 3.11 or newer wasn't found. Install it with your package manager, then run ./setup.sh again."
echo "    Using: $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"

# ---------------------------------------------------------------- virtual environment
step "Creating the Python environment (.venv)"
if [ ! -x .venv/bin/python ]; then
    # System site packages make a distro-installed PySide6 visible (PyPI has no build for musl, e.g. Void/Alpine).
    "$PYTHON" -m venv --system-site-packages .venv || fail "Couldn't create the virtual environment (Debian/Ubuntu: sudo apt install python3-venv)."
fi
PY="$ROOT/.venv/bin/python"

step "Installing Python packages (first run can take a few minutes)"
"$PY" -m pip install --upgrade pip --disable-pip-version-check -q || fail "pip upgrade failed. Check your internet connection."
"$PY" -m pip install -r backend/requirements-core.txt --disable-pip-version-check \
    || fail "Installing packages failed. Scroll up for the error; a network or proxy issue is the usual cause."

if ! "$PY" -c 'import PySide6.QtWidgets, PySide6.QtNetwork' 2>/dev/null; then
    echo "    PySide6 isn't installed system-wide; trying the pip package..."
    if ! "$PY" -m pip install "PySide6-Essentials==6.11.2" --disable-pip-version-check -q 2>/dev/null; then
        fail "PySide6 (tray icon and note window) isn't available from pip for this system. Install it with:
      $(qt_hint)
  then run ./setup.sh again."
    fi
fi

# ---------------------------------------------------------------- system tools
step "Checking desktop integration"
if command -v pactl >/dev/null && command -v parec >/dev/null; then
    echo "    Audio: $(pactl info 2>/dev/null | sed -n 's/^Server Name: //p')"
else
    warn "pactl/parec not found - audio recording is disabled. Install pulseaudio-utils (or your distro's pactl package)."
fi
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    echo "    Wayland session: screenshots go through xdg-desktop-portal."
    ls /usr/share/xdg-desktop-portal/portals/*.portal >/dev/null 2>&1 || warn "No xdg-desktop-portal backend found - screenshots won't work."
fi

# ---------------------------------------------------------------- interface
step "Building the interface"
if ! command -v node >/dev/null; then
    if [ -f frontend/dist/index.html ]; then
        warn "Node.js not found - using the existing prebuilt interface."
    else
        fail "Node.js 20+ is needed to build the interface. Install it with your package manager and run ./setup.sh again."
    fi
else
    NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
    [ "$NODE_MAJOR" -ge 20 ] || fail "Node.js $(node -v) is too old; install Node.js 20 or newer."
    (cd frontend && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi && npm run build) \
        || fail "Building the interface failed (see above)."
fi

# ---------------------------------------------------------------- config
if [ ! -f .env ]; then cp .env.example .env; echo "    Created .env from .env.example"; fi

step "Checking the installation"
(cd backend && "$PY" -m checkpoint.preflight) || fail "Preflight reported errors (see above)."

TRIGGER="$PY $ROOT/backend/checkpoint/trigger.py"
printf '\n\033[32mSetup complete.\033[0m\n'
echo "  Start Checkpoint:  ./start.sh"
echo ""
echo "  Global shortcuts are set in your desktop's keyboard settings (COSMIC: Settings > Input Devices >"
echo "  Keyboard > Keyboard shortcuts > Custom shortcuts). Add one per command"
echo "  (the keys are suggestions):"
echo "      F9          $TRIGGER capture   (tap; press twice to edit the last task)"
echo "      Shift+F9    $TRIGGER review    (open the menu)"
echo "      Ctrl+F9     $TRIGGER voice     (start a voice note; press again to save)"
echo ""
echo "  Optional: download the speech model in Settings > Transcript for voice notes."
echo ""
