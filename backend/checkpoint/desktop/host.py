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
from .overlay import ReviewOverlay
from .switcher import ProjectSwitcher
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
    offer_requested = Signal(dict)
    review_requested = Signal(dict)
    switch_requested = Signal(dict)


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
        self.overlay = ReviewOverlay(self.core.previews)
        self.bridge.review_requested.connect(self._toggle_overlay)
        self.switcher = ProjectSwitcher(self.core.sessions, self._switched)
        self.switcher.model.on_switched = self._on_switched
        self.bridge.switch_requested.connect(self._toggle_switcher)
        self.bridge.note_requested.connect(self.note_window.open_for)
        self.bridge.toast_requested.connect(lambda lvl, msg, work: self.toast.show_message(lvl, msg, work))
        self.bridge.state_changed.connect(self._refresh_tray)
        self.bridge.offer_requested.connect(self._show_offer)
        self._end_armed_at = 0.0
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
        hooks.offer_session = lambda offer: self.bridge.offer_requested.emit(offer)

    def _rebind(self) -> dict:
        if not self.hotkeys:
            return {"ok": False, "reason": "Hotkeys unavailable"}
        self.hotkeys.rebind()
        return self.hotkeys.summary()

    def _on_hotkey(self, action: str) -> None:
        """Runs on the hotkey thread. Capture first, UI after."""
        mono = time.monotonic()
        if action in ("review", "switch_project"):
            self._overlay_hotkey(action)
            return
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
        elif action == "start_session":
            self._start_or_end_hotkey()
        elif action == "quick_note":
            from ..capture import win32

            fg = win32.foreground_info()
            self.bridge.note_requested.emit({
                "id": None, "mono": mono, "fg_hwnd": fg.hwnd if fg else 0,
                "work_rect": fg.work_rect.as_mss() if fg and fg.work_rect else None,
            })

    # ------------------------------------------------------------ review overlay
    def _overlay_hotkey(self, action: str) -> None:
        """Hotkey thread: note what's in the foreground (to infer the project), then show the overlay or switcher."""
        from ..capture import win32

        fg = win32.foreground_info()
        own = fg is not None and fg.pid == win32.current_pid()
        window = None
        if fg is not None and not own:
            window = win32.foreground_window()
        signal = self.bridge.review_requested if action == "review" else self.bridge.switch_requested
        signal.emit({
            "window": window, "fg_hwnd": fg.hwnd if fg and not own else 0,
            "work_rect": fg.work_rect.as_mss() if fg and fg.work_rect else None,
        })

    def _toggle_overlay(self, payload: dict) -> None:
        if self.note_window.isVisible():
            hooks.emit_toast("warning", "Finish or press Esc on the open note first.")
            return
        if self.switcher.isVisible():  # pressed over the switcher: take over the window it came from
            payload = {**payload, "fg_hwnd": payload.get("fg_hwnd") or self.switcher.fg_hwnd,
                       "work_rect": payload.get("work_rect") or self.switcher.work}
            self.switcher.hide_switcher(restore=False)
        a = self.core.sessions.active
        self.overlay.toggle(a.id if a else None, payload.get("window"), payload.get("work_rect"), payload.get("fg_hwnd") or 0)

    # ------------------------------------------------------------ project switcher
    def _toggle_switcher(self, payload: dict) -> None:
        if self.note_window.isVisible():
            hooks.emit_toast("warning", "Finish or press Esc on the open note first.")
            return
        if self.overlay.isVisible():  # pressed over the review overlay: take over the window it came from
            payload = {**payload, "fg_hwnd": payload.get("fg_hwnd") or self.overlay.fg_hwnd,
                       "work_rect": payload.get("work_rect") or self.overlay.work}
            self.overlay.hide_overlay(restore=False)
        self.switcher.toggle(payload.get("window"), payload.get("work_rect"), payload.get("fg_hwnd") or 0)

    def _on_switched(self, _pid: str, _sid: str, ended: Optional[str]) -> None:
        self.core.appwatch.dismiss()
        if ended:
            self.core.transcriber.wake()
            self.core.organiser.wake()
        self.bridge.state_changed.emit()

    def _switched(self, msg: str, work: Optional[dict]) -> None:
        st = self.core.sessions.status()
        srcs = ", ".join(s["label"] for s in st.get("sources", [])) or "no audio"
        self.toast.show_message("success", f"{msg} · {srcs}", work)

    # ------------------------------------------------------------ one-key sessions
    def _hotkey_label(self) -> str:
        return get_settings().hotkeys.start_session

    def _start_or_end_hotkey(self) -> None:
        """Start a session with the remembered setup; pressed twice within 3 s while one runs, end it."""
        if self.core.sessions.active is None:
            self._quick_start()
            return
        now = time.monotonic()
        if now - self._end_armed_at < 3:
            self._end_armed_at = 0.0
            self.core.sessions.end()
            self.core.transcriber.wake()
            self.core.organiser.wake()
            hooks.emit_toast("info", "Session ended. Saved content will be processed in the background.")
            self.bridge.state_changed.emit()
        else:
            self._end_armed_at = now
            hooks.emit_toast("info", f"Session running. Press {self._hotkey_label()} again to end it.")

    def _quick_start(self, project_id: Optional[str] = None) -> None:
        try:
            self.core.quick_start(project_id)
        except ValueError as e:
            hooks.emit_toast("warning", str(e))
            return
        st = self.core.sessions.status()
        srcs = ", ".join(s["label"] for s in st.get("sources", [])) or "no audio (F8 asks for a note)"
        hooks.emit_toast("success", f"Session started · recording {srcs}. Change it any time from the tray.")
        self.bridge.state_changed.emit()

    def _show_offer(self, offer: dict) -> None:
        msg = f"{offer['program']} is running. Press {self._hotkey_label()} to start a \u201c{offer['project_name']}\u201d session."
        self.toast.show_message("info", msg, None, duration_ms=12000)
        self.tray.showMessage("Start a session?", msg + " Or click here.", self.icons["session"], 12000)

    def _on_message_clicked(self) -> None:
        if self.core.appwatch.offer and self.core.sessions.active is None:
            self._quick_start()

    def _waiting_hint(self) -> str:
        from ..services.flow import waiting_count

        a = self.core.sessions.active
        n = waiting_count(a.id if a else None, None if a else get_settings().last_project_id)
        return f" · {n} to review ({get_settings().hotkeys.review})" if n else ""

    def _save_note(self, payload: dict, text: str, category: Optional[str]) -> Optional[str]:
        try:
            if payload.get("id"):
                self.core.capture.save_capture_note(payload["id"], text, category)
                self.toast.show_message("success", "Saved as a card" + self._waiting_hint(), payload.get("work_rect"))
            else:
                self.core.capture.quick_note(text, category, mono=payload.get("mono"))
                self.toast.show_message("success", "Note saved as a card" + self._waiting_hint(), payload.get("work_rect"))
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
        self.act_start = QAction("Start session", triggered=lambda: self._quick_start())
        self.act_review = QAction("Review and send…", triggered=lambda: self._toggle_overlay({}))
        self.act_switch = QAction("Switch project…", triggered=lambda: self._toggle_switcher({}))
        self.act_pause = QAction("Pause recording", triggered=self._toggle_pause)
        self.act_mic = QAction("Record microphone", checkable=True, triggered=lambda on: self._toggle_source("mic", on))
        self.act_loop = QAction("Record computer audio", checkable=True, triggered=lambda on: self._toggle_source("loopback", on))
        self.act_auto = QAction("Auto screenshots", checkable=True, triggered=self._toggle_auto)
        self.act_end = QAction("End session", triggered=self._end_session)
        self.act_quit = QAction("Quit", triggered=self.quit)
        for a in (self.act_open, self.act_review, self.act_switch, None, self.act_status, self.act_start, self.act_pause, self.act_mic, self.act_loop, self.act_auto,
                  self.act_end, None, self.act_quit):
            if a is None:
                menu.addSeparator()
            else:
                menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.messageClicked.connect(self._on_message_clicked)
        self.tray.activated.connect(lambda reason: self.open_ui() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        self._menu = menu
        self.timer = QTimer(interval=1000, timeout=self._refresh_tray)
        self.timer.start()
        self._refresh_tray()

    def _refresh_tray(self) -> None:
        st = self.core.sessions.status()
        live = bool(st.get("active"))
        try:
            from ..services.flow import waiting_count

            n = waiting_count(st.get("session_id"), None if live else get_settings().last_project_id)
        except Exception:  # noqa: BLE001
            n = 0
        hk = get_settings().hotkeys.review
        self.act_review.setText(f"Review and send ({n} waiting)  ({hk})" if n else f"Review and send  ({hk})")
        self.act_switch.setText(f"Switch project…  ({get_settings().hotkeys.switch_project})")
        for a in (self.act_pause, self.act_mic, self.act_loop, self.act_auto, self.act_end):
            a.setVisible(live)
        self.act_start.setVisible(not live)
        if not live:
            self.tray.setIcon(self.icons["idle"])
            self.tray.setToolTip("Checkpoint — no session")
            self.act_status.setText("No session running")
            offer = self.core.appwatch.offer
            name = offer["project_name"] if offer else self._last_project_name()
            self.act_start.setText(f"Start session: {name}  ({self._hotkey_label()})" if name else "Start session")
            self.act_start.setEnabled(bool(name))
            return
        kinds = {x["kind"] for x in st["sources"]}
        self.act_mic.setChecked("mic" in kinds)
        self.act_loop.setChecked("loopback" in kinds)
        self.act_auto.setChecked(get_settings().auto_capture.enabled)
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

    def _last_project_name(self) -> Optional[str]:
        from ..db import read_session
        from ..models import Project

        pid = get_settings().last_project_id
        if not pid:
            return None
        with read_session() as s:
            p = s.get(Project, pid)
            return p.name if p and not p.archived and not p.is_demo else None

    def _toggle_source(self, kind: str, on: bool) -> None:
        try:
            self.core.sessions.set_source(kind, on)
        except ValueError as e:
            hooks.emit_toast("warning", str(e))
        self._refresh_tray()

    def _toggle_auto(self, on: bool) -> None:
        from ..settings_store import update_settings

        update_settings({"auto_capture": {"enabled": on}})
        self.core.autocapture.wake()
        self._refresh_tray()

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
            return {"capture": hk.capture, "capture_context": hk.capture_context, "quick_note": hk.quick_note,
                    "start_session": hk.start_session, "review": hk.review, "switch_project": hk.switch_project}

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
