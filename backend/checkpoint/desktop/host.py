"""Desktop host: single instance, tray icon, global hotkeys, native note window.

Threads
- Qt main thread: tray, note window, toasts (all widgets live here).
- hotkeys thread: Win32 message loop; grabs the screenshot *immediately* on the
  key press, then signals the Qt thread to show UI.
- api-server thread: uvicorn serving the local UI.
- workers: transcription / organiser / sync, supervised by the core.
"""

from __future__ import annotations

import getpass
import logging
import sys
import time
import webbrowser
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from ..api.app import ServerThread, create_app, dev_origins, pick_port
from ..api.security import AuthState
from ..config import DEFAULT_PORT
from ..core import get_core
from ..events import hooks, notices
from ..settings_store import get_settings
from .windows import NoteWindow, Toast

log = logging.getLogger(__name__)
INSTANCE_KEY = f"Checkpoint-{getpass.getuser()}"


def _icon(color: str, ring: Optional[str] = None) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#1d2027"))
    p.setPen(QColor("#5b8cff"))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(20, 20, 24, 24)
    if ring:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(ring))
        p.drawEllipse(14, 14, 36, 36)
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    """Signals are delivered on the Qt main thread (queued across threads)."""

    note_requested = Signal(dict)
    toast_requested = Signal(str, str, object)
    state_changed = Signal()


class DesktopHost:
    def __init__(self, open_browser: bool = True):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("Checkpoint")
        self.open_browser = open_browser
        self.core = get_core()
        self.bridge = Bridge()
        self.note_window = NoteWindow(self._save_note, self._keep_capture, self._discard_capture)
        self.toast = Toast()
        self.bridge.note_requested.connect(self.note_window.open_for)
        self.bridge.toast_requested.connect(lambda lvl, msg, work: self.toast.show_message(lvl, msg, work))
        self.bridge.state_changed.connect(self._refresh_tray)
        self.hotkeys = None
        self.server: Optional[ServerThread] = None
        self.auth: Optional[AuthState] = None
        self.local_server = QLocalServer()
        self._quitting = False

    # ------------------------------------------------------------ single instance
    @staticmethod
    def signal_existing() -> bool:
        sock = QLocalSocket()
        sock.connectToServer(INSTANCE_KEY)
        if sock.waitForConnected(500):
            sock.write(b"open\n")
            sock.flush()
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            return True
        return False

    def _listen(self) -> None:
        QLocalServer.removeServer(INSTANCE_KEY)
        self.local_server.listen(INSTANCE_KEY)
        self.local_server.newConnection.connect(self._on_instance_msg)

    def _on_instance_msg(self) -> None:
        conn = self.local_server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(300)
        if b"open" in bytes(conn.readAll()):
            self.open_ui()
        conn.disconnectFromServer()

    # ------------------------------------------------------------ hooks for the core
    def _install_hooks(self) -> None:
        hooks.request_note = lambda payload: self.bridge.note_requested.emit(payload)
        hooks.toast = lambda level, msg, work=None: self.bridge.toast_requested.emit(level, msg, work)
        hooks.state_changed = lambda: self.bridge.state_changed.emit()
        hooks.note_window_open = lambda: self.note_window.isVisible()
        hooks.hotkey_status = lambda: self.hotkeys.summary() if self.hotkeys else {"ok": False, "reason": "Hotkeys unavailable"}
        hooks.rebind_hotkeys = self._rebind

    def _rebind(self) -> dict:
        if not self.hotkeys:
            return {"ok": False, "reason": "Hotkeys unavailable"}
        self.hotkeys.rebind()
        return self.hotkeys.summary()

    def _on_hotkey(self, action: str) -> None:
        """Runs on the hotkey thread. Capture first, UI after."""
        mono = time.monotonic()
        if self.note_window.isVisible():
            # Never capture our own popup; ask the user to finish the open note.
            hooks.emit_toast("warning", "Finish or press Esc on the open note first.")
            return
        cap = self.core.capture
        if cap.debounce(action):
            return
        if action == "capture":
            cap.hotkey_capture(ask_context=False)
        elif action == "capture_context":
            cap.hotkey_capture(ask_context=True)
        elif action == "quick_note":
            from ..capture import win32

            fg = win32.foreground_info()
            self.bridge.note_requested.emit({
                "id": None, "mono": mono, "fg_hwnd": fg.hwnd if fg else 0,
                "work_rect": fg.work_rect.as_mss() if fg and fg.work_rect else None,
            })

    def _save_note(self, payload: dict, text: str, category: Optional[str]) -> Optional[str]:
        try:
            if payload.get("id"):
                self.core.capture.save_capture_note(payload["id"], text, category)
                self.toast.show_message("success", "Saved as a card for review", payload.get("work_rect"))
            else:
                self.core.capture.quick_note(text, category, mono=payload.get("mono"))
                self.toast.show_message("success", "Note saved as a card for review", payload.get("work_rect"))
        except ValueError as e:
            return str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("Saving note failed")
            return f"Couldn't save: {e}"
        return None

    def _keep_capture(self, payload: dict) -> None:
        if payload.get("id"):
            self.core.capture.keep_unfinished(payload["id"])
            self.toast.show_message("info", "Kept in Unfinished captures", payload.get("work_rect"))

    def _discard_capture(self, payload: dict) -> Optional[str]:
        try:
            self.core.capture.discard(payload["id"])
        except ValueError as e:
            return str(e)
        return None

    # ------------------------------------------------------------ tray
    def _build_tray(self) -> None:
        self.icons = {
            "idle": _icon("#6b7280"),
            "recording": _icon("#f07171"),
            "session": _icon("#5b8cff"),
            "paused": _icon("#e0a84e"),
            "problem": _icon("#f07171", ring="#e0a84e"),
        }
        self.tray = QSystemTrayIcon(self.icons["idle"])
        menu = QMenu()
        self.act_open = QAction("Open Checkpoint", triggered=self.open_ui)
        self.act_status = QAction("No session running")
        self.act_status.setEnabled(False)
        self.act_pause = QAction("Pause recording", triggered=self._toggle_pause)
        self.act_end = QAction("End session", triggered=self._end_session)
        self.act_quit = QAction("Quit", triggered=self.quit)
        for a in (self.act_open, None, self.act_status, self.act_pause, self.act_end, None, self.act_quit):
            if a is None:
                menu.addSeparator()
            else:
                menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.open_ui() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        self._menu = menu
        self.timer = QTimer(interval=1000, timeout=self._refresh_tray)
        self.timer.start()
        self._refresh_tray()

    def _refresh_tray(self) -> None:
        st = self.core.sessions.status()
        if not st.get("active"):
            self.tray.setIcon(self.icons["idle"])
            self.tray.setToolTip("Checkpoint — no session")
            self.act_status.setText("No session running")
            self.act_pause.setVisible(False)
            self.act_end.setVisible(False)
            return
        srcs = st["sources"]
        problems = [s for s in srcs if s["state"] in ("failed", "reconnecting") or s.get("error")]
        recording = [s for s in srcs if s["state"] in ("recording", "idle")]
        if st["paused"]:
            key, label = "paused", "Paused — audio not recording"
        elif problems:
            key, label = "problem", "Audio problem: " + ", ".join(f"{s['label']} {s['state']}" for s in problems)
        elif recording:
            key, label = "recording", "Recording: " + ", ".join(s["label"] for s in recording)
        else:
            key, label = "session", "Session running — no audio (F8 asks for a note)"
        self.tray.setIcon(self.icons[key])
        self.tray.setToolTip(f"Checkpoint — {st['title']}\n{label}"[:120])
        self.act_status.setText(label[:80])
        self.act_pause.setVisible(True)
        self.act_end.setVisible(True)
        self.act_pause.setText("Resume recording" if st["paused"] else "Pause recording")

    def _toggle_pause(self) -> None:
        st = self.core.sessions.status()
        if st.get("paused"):
            self.core.sessions.resume()
        else:
            self.core.sessions.pause()
        self._refresh_tray()

    def _end_session(self) -> None:
        self.core.sessions.end()
        self.core.transcriber.wake()
        self.core.organiser.wake()
        self.tray.showMessage("Session ended", "Saved content will be processed in the background.", self.icons["idle"], 3000)
        self._refresh_tray()

    def open_ui(self, path: str = "/") -> None:
        if self.auth:
            webbrowser.open(self.auth.open_url(path))

    def quit(self) -> None:
        if self._quitting:
            return
        if self.core.sessions.active is not None:
            r = QMessageBox.question(None, "Quit Checkpoint",
                                     "A session is running. End the session and quit?\n\nEverything captured so far is saved.")
            if r != QMessageBox.StandardButton.Yes:
                return
        self._quitting = True
        self.shutdown()
        self.app.quit()

    def shutdown(self) -> None:
        try:
            if self.hotkeys:
                self.hotkeys.stop()
        finally:
            self.core.shutdown()
            if self.server:
                self.server.stop()
            self.local_server.close()

    # ------------------------------------------------------------ run
    def run(self) -> int:
        from .hotkeys import HotkeyThread

        self._listen()
        self.core.start()
        port = pick_port(DEFAULT_PORT)
        self.auth = AuthState(port, dev_origins())
        self.server = ServerThread(create_app(self.auth), port)
        self.server.start()
        if not self.server.wait_started():
            QMessageBox.critical(None, "Checkpoint", "The local server couldn't start. See the log in the data folder.")
            self.core.shutdown()
            return 1
        self._install_hooks()

        def bindings() -> dict[str, str]:
            hk = get_settings().hotkeys
            return {"capture": hk.capture, "capture_context": hk.capture_context, "quick_note": hk.quick_note}

        self.hotkeys = HotkeyThread(bindings, self._on_hotkey)
        self.hotkeys.start()
        summary = self.hotkeys.summary()
        self._build_tray()
        if not summary["ok"]:
            msg = summary.get("reason") or "Some shortcuts couldn't be registered."
            notices.push("warning", f"Shortcut problem: {msg}. Change them in Settings → Shortcuts.", "hotkeys")
            self.tray.showMessage("Shortcut conflict", msg[:200], QSystemTrayIcon.MessageIcon.Warning, 8000)
        self.app.aboutToQuit.connect(lambda: None if self._quitting else self.shutdown())
        log.info("Desktop host running on port %s", port)
        print(f"Checkpoint is running at http://127.0.0.1:{port} (tray icon > Open)")
        if self.open_browser:
            self.open_ui()
        return self.app.exec()


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    app = QApplication.instance() or QApplication(sys.argv)
    if DesktopHost.signal_existing():
        print("Checkpoint is already running — opened the existing window.")
        return 0
    host = DesktopHost(open_browser="--no-browser" not in argv)
    return host.run()
