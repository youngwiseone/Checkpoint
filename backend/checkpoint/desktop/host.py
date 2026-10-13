"""Desktop host: single instance, tray icon, global hotkeys, native note window.

Threads
- Qt main thread: tray, note window, toasts (all widgets live here).
- hotkeys thread: Win32 message loop; grabs the screenshot *immediately* on the
  key press, then signals the Qt thread to show UI.
- workers: transcription / organiser / sync, supervised by the core.
"""

from __future__ import annotations

import getpass
import logging
import sys
import time
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..core import get_core
from ..events import hooks, notices
from ..settings_store import get_settings
from . import colors
from .inbox import InboxPanel
from .gestures import CaptureButton
from .windows import NoteWindow, Toast

log = logging.getLogger(__name__)
INSTANCE_KEY = f"Checkpoint-{getpass.getuser()}"


def _icon(color: str, ring: Optional[str] = None) -> QIcon:
    """A tray icon in the theme's colours (as it was at startup): a dot of `color` (a palette key) on a rounded tile."""
    c = colors.current()
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(c.surface_2)
    p.setPen(c.accent)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(getattr(c, color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(20, 20, 24, 24)
    if ring:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(getattr(c, ring))
        p.drawEllipse(14, 14, 36, 36)
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    """Signals are delivered on the Qt main thread (queued across threads)."""

    note_requested = Signal(dict)
    toast_requested = Signal(str, str, object)
    state_changed = Signal()
    review_requested = Signal(dict)
    held_note = Signal(object)
    setup_requested = Signal(str)


class DesktopHost:
    def __init__(self, show_panel: bool = True):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("Checkpoint")
        self.show_panel = show_panel
        self.core = get_core()
        self.bridge = Bridge()
        self.note_window = NoteWindow(self._save_note, self._keep_capture, self._discard_capture, self._menu_from_note)
        self.toast = Toast()
        self.overlay = InboxPanel(open_settings=self.open_settings, recording=self._record_from_panel,
                                  recording_status=self.core.sessions.status,
                                  processing_status=lambda: self.core.worker_status()["transcription"],
                                  new_session=self._new_session_from_panel)
        self.bridge.review_requested.connect(self._toggle_overlay)
        self.bridge.note_requested.connect(self.note_window.open_for)
        self.bridge.toast_requested.connect(lambda lvl, msg, work: self.toast.show_message(lvl, msg, work))
        self.bridge.state_changed.connect(self._refresh_tray)
        self.capture_button = CaptureButton()
        self._prepared_press = None
        self._press_recording = None
        self._held_jobs = []
        self.bridge.held_note.connect(self._track_held_note)
        self.bridge.setup_requested.connect(self._prompt_voice_setup)
        self.held_timer = QTimer(self.bridge, interval=100, timeout=self._poll_held_notes)
        self.hotkeys = None
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

    def _latest_note(self):
        from ..services.task_evidence import last_editor_payload
        active = self.core.sessions.active
        pid = active.project_id if active else get_settings().last_project_id
        payload = last_editor_payload(pid) if pid else None
        if not payload:
            self.capture_button.previous = None
        return payload

    def _on_hotkey(self, action: str) -> None:
        """Hotkey thread: freeze the initial frame and record until release."""
        if action == 'review':
            self.capture_button.clear()
            self._overlay_hotkey(action)
            return
        if action == 'press':
            self._prepared_press = None
            self._press_recording = None
            if self.note_window.isVisible() or self.overlay.isVisible() or self.app.activeModalWidget() is not None:
                return
            from ..capture import screen
            from datetime import datetime, timezone
            active = self.core.sessions.active
            mic_recorder = active.recorders.get('mic') if active else None
            if get_settings().audio.mic_enabled or mic_recorder:
                from ..audio.dictation import NoteDictation
                self._press_recording = NoteDictation(getattr(mic_recorder, 'device_name', get_settings().audio.mic_device))
                self._press_recording.project_id = active.project_id if active else get_settings().last_project_id
                self._press_recording.start()
            mono, taken = time.monotonic(), datetime.now(timezone.utc)
            try:
                self._prepared_press = (screen.grab(active.capture_target if active else 'foreground', get_settings().capture.region), mono, taken)
            except Exception as error:
                hooks.emit_toast('error', f'Screenshot failed: {error}')
            return
        if action == 'release':
            if self._press_recording:
                self._press_recording.stop()
                self._press_recording = None
            return
        if action == 'hold':
            self.capture_button.clear()
            prepared = self._prepared_press
            self._prepared_press = None
            if not prepared:
                if self._press_recording:
                    self._press_recording.cancel()
                return
            payload = self.core.capture.hotkey_capture(False, prepared=prepared, quiet=True)
            if not payload:
                if self._press_recording:
                    self._press_recording.cancel()
                return
            self.capture_button.previous = payload
            recording = self._press_recording
            if recording:
                self.bridge.held_note.emit((payload, recording))
                hooks.emit_toast('info', 'Listening… release the capture key to save your voice note.', payload.get('work_rect'),)
            else:
                self.bridge.note_requested.emit(payload)
                self.bridge.setup_requested.emit('Audio')
            return
        if action == 'capture':
            if getattr(self, '_press_recording', None):
                self._press_recording.cancel()
                self._press_recording = None
            prepared = getattr(self, '_prepared_press', None)
            self._prepared_press = None
            def grab():
                if self.note_window.isVisible() or self.overlay.isVisible() or self.app.activeModalWidget() is not None:
                    return None
                active = self.core.sessions.active
                has_audio = bool(active and self.core.sessions.audio_functioning() and any(
                    st['state'] in ('recording', 'idle') and st['receiving'] and st['signal_recent']
                    for recorder in active.recorders.values() for st in (recorder.status(),)))
                return self.core.capture.hotkey_capture(ask_context=not has_audio,
                    reason=None if has_audio else 'no_audio', **({'prepared': prepared} if prepared else {}))
            self.capture_button.tap(grab, self.bridge.note_requested.emit, time.monotonic,
                                    review=lambda: self._overlay_hotkey('review'), latest=self._latest_note)

    def _track_held_note(self, job):
        self._held_jobs.append(job)
        self.held_timer.start()

    def _poll_held_notes(self):
        for payload, recording in list(self._held_jobs):
            from ..services.voice import retain, finish_note
            if recording.audio and not recording.retained_note_id:
                try:
                    recording.retained_note_id = retain(payload['id'], recording.audio)
                except (ValueError, OSError) as error:
                    hooks.emit_toast('error', f'Could not retain voice recording: {error}')
                    continue
            if recording.phase != 'done':
                continue
            self._held_jobs.remove((payload, recording))
            try:
                if recording.retained_note_id:
                    finish_note(recording.retained_note_id, recording.text, recording.words)
                if recording.text:
                    from ..services.task_evidence import capture_editor_payload
                    current = capture_editor_payload(payload['id'])
                    from ..services.review import get_work_item
                    existing = get_work_item(current['item_id'])
                    text = '\n\n'.join(p for p in (existing['description'] if existing['description_edited'] else '', recording.text) if p)
                    self.core.capture.save_capture_note(payload['id'], text)
                    if self.note_window.isVisible() and self.note_window.payload.get('item_id') == current['item_id']:
                        editor_text = self.note_window.text.toPlainText()
                        if recording.text not in editor_text:
                            self.note_window.text.setPlainText('\n\n'.join(p for p in (editor_text, recording.text) if p))
                        self.note_window._reload_evidence()
                    hooks.emit_toast('success', 'Voice note saved · double tap to edit.', payload.get('work_rect'))
                else:
                    self.bridge.note_requested.emit(payload)
                    hooks.emit_toast('warning', recording.error or 'No speech detected. Add a note or keep the screenshot.', payload.get('work_rect'))
                    if recording.error:
                        from ..transcription.engine import model_installed
                        self.bridge.setup_requested.emit('Transcript' if not model_installed('whistle') else 'Audio')
                self.core.inbox.wake()
            except Exception as error:
                hooks.emit_toast('error', f'Could not save voice note: {error}')
                self.bridge.note_requested.emit(payload)
        if not self._held_jobs:
            self.held_timer.stop()

    def _prompt_voice_setup(self, tab):
        from .glass import GlassDialog
        from PySide6.QtWidgets import QLabel, QPushButton
        dialog = GlassDialog(self.note_window)
        dialog.setWindowTitle('Set up voice notes')
        dialog.resize(420, 180)
        layout = dialog.content_layout()
        message = QLabel('Enable your microphone in Audio settings to record while holding the capture key.' if tab == 'Audio'
                         else 'Your recording is saved. Download the speech model in Transcript settings to turn voice notes into text.')
        message.setWordWrap(True)
        layout.addWidget(message)
        button = QPushButton('Open ' + tab + ' settings')
        button.clicked.connect(lambda: (dialog.accept(), self.open_settings(tab)))
        layout.addWidget(button)
        later = QPushButton('Later')
        later.clicked.connect(dialog.reject)
        layout.addWidget(later)
        dialog.exec()

    def _menu_from_note(self):
        if self.overlay.isVisible():
            self.overlay.raise_()
            self.overlay.activateWindow()
        else:
            self._toggle_overlay({})

    # ------------------------------------------------------------ review overlay
    def _overlay_hotkey(self, action: str) -> None:
        """Hotkey thread: note what's in the foreground (to infer the project), then show the overlay or switcher."""
        from ..capture import win32

        fg = win32.foreground_info()
        own = fg is not None and fg.pid == win32.current_pid()
        window = None
        if fg is not None and not own:
            window = win32.foreground_window()
        signal = self.bridge.review_requested
        signal.emit({
            "window": window, "fg_hwnd": fg.hwnd if fg and not own else 0,
            "work_rect": fg.work_rect.as_mss() if fg and fg.work_rect else None,
        })

    def _toggle_overlay(self, payload: dict) -> None:
        if self.app.activeModalWidget() is not None:
            return
        if self.note_window.isVisible():
            if self.note_window._dictation is not None:
                hooks.emit_toast('info', 'Finish the voice note before opening the menu.')
                return
            if not self.note_window._persist_editor():
                return
            self.note_window._close()
        a = self.core.sessions.active
        self.overlay.toggle(a.project_id if a else get_settings().last_project_id, payload.get("work_rect"))

    def _quick_start(self, project_id: Optional[str] = None) -> None:
        try:
            self.core.quick_start(project_id)
        except ValueError as e:
            hooks.emit_toast("warning", str(e))
            return
        st = self.core.sessions.status()
        srcs = ", ".join(s["label"] for s in st.get("sources", [])) or "no audio"
        hooks.emit_toast("success", f"Session started · recording {srcs}. Change it any time from the tray.")
        self.bridge.state_changed.emit()

    def _new_session_from_panel(self, project_id: str) -> None:
        from ..services.sessions import remembered_setup

        # Read the setup before ending so validation errors leave the running session intact.
        from ..db import read_session
        from ..models import Project
        with read_session() as session:
            project = session.get(Project, project_id)
            if project is None or project.archived or project.is_demo:
                raise ValueError("Choose an available project before starting a session.")
        setup = remembered_setup(project_id)
        self.core.sessions.end()
        self.core.sessions.start(setup)
        self._refresh_tray()

    def _record_from_panel(self):
        if self.core.sessions.active:
            self._end_session()
            return
        from ..services.sessions import SourceConfig, StartSessionRequest
        st = get_settings()
        if not st.last_project_id:
            self.open_settings()
            return
        a = st.audio
        try:
            self.core.sessions.start(StartSessionRequest(project_id=st.last_project_id,
                mic=SourceConfig(enabled=a.mic_enabled, device=a.mic_device, label=a.mic_label),
                loopback=SourceConfig(enabled=a.loopback_enabled, device=a.loopback_device, label=a.loopback_label),
                transcription_mode=st.transcription.default_mode, ai_enabled=False))
            self._refresh_tray()
        except ValueError as error:
            hooks.emit_toast("warning", str(error))

    def _on_message_clicked(self) -> None:
        self.open_ui()

    def _waiting_hint(self) -> str:
        return ""

    def _save_note(self, payload: dict, text: str, category: Optional[str]) -> Optional[str]:
        try:
            if payload.get('item_id'):
                from ..services.review import update_work_item
                update_work_item(payload['item_id'], {'description': text,
                                                      **({'title': text.splitlines()[0][:300]} if text.strip() else {}),
                                                      **({'type': category} if category else {})})
                self.toast.show_message('success', 'Note saved', payload.get('work_rect'))
            elif payload.get("id"):
                self.core.capture.save_capture_note(payload["id"], text, category)
                self.toast.show_message("success", "Saved to inbox" + self._waiting_hint(), payload.get("work_rect"))
            else:
                self.core.capture.quick_note(text, category, mono=payload.get("mono"), window=payload.get("window"))
                self.toast.show_message("success", "Note saved to inbox" + self._waiting_hint(), payload.get("work_rect"))
        except ValueError as e:
            return str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("Saving note failed")
            return f"Couldn't save: {e}"
        return None

    def _keep_capture(self, payload: dict) -> None:
        if payload.get("id"):
            self.core.capture.keep_unfinished(payload["id"])
            self.toast.show_message("info", "Kept in inbox", payload.get("work_rect"))

    def _discard_capture(self, payload: dict) -> Optional[str]:
        try:
            from ..services.inbox import ensure_capture
            from ..services.review import update_work_item
            update_work_item(ensure_capture(payload["id"]), {"work_status": "wont_do"})
        except ValueError as e:
            return str(e)
        return None

    # ------------------------------------------------------------ tray
    def _build_tray(self) -> None:
        self.icons = {
            "idle": _icon("muted"),
            "recording": _icon("danger"),
            "session": _icon("accent"),
            "paused": _icon("warn"),
            "problem": _icon("danger", ring="warn"),
        }
        self.tray = QSystemTrayIcon(self.icons["idle"])
        menu = QMenu()
        self.act_open = QAction("Open Checkpoint", triggered=self.open_ui)
        self.act_status = QAction("No session running")
        self.act_status.setEnabled(False)
        self.act_start = QAction("Start recording", triggered=self._record_from_panel)
        self.act_settings = QAction("Settings", triggered=self.open_settings)
        self.act_review = QAction("Review inbox", triggered=lambda: self._toggle_overlay({}))
        self.act_pause = QAction("Pause recording", triggered=self._toggle_pause)
        self.act_mic = QAction("Record microphone", checkable=True, triggered=lambda on: self._toggle_source("mic", on))
        self.act_loop = QAction("Record computer audio", checkable=True, triggered=lambda on: self._toggle_source("loopback", on))
        self.act_end = QAction("End session", triggered=self._end_session)
        self.act_quit = QAction("Quit", triggered=self.quit)
        for a in (self.act_open, self.act_settings, None, self.act_status, self.act_start, self.act_pause, self.act_mic, self.act_loop,
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
        hk = get_settings().hotkeys.review
        self.act_review.setText(f"Review inbox  ({hk + ', ' if hk else ''}triple tap capture)")
        for a in (self.act_pause, self.act_mic, self.act_loop, self.act_end):
            a.setVisible(live)
        self.act_start.setVisible(not live)
        if not live:
            self.tray.setIcon(self.icons["idle"])
            self.tray.setToolTip("Checkpoint — no session")
            self.act_status.setText("No session running")
            offer = self.core.appwatch.offer
            name = offer["project_name"] if offer else self._last_project_name()
            self.act_start.setText(f"Start recording: {name}" if name else "Start session")
            self.act_start.setEnabled(bool(name))
            return
        kinds = {x["kind"] for x in st["sources"]}
        self.act_mic.setChecked("mic" in kinds)
        self.act_loop.setChecked("loopback" in kinds)
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
            key, label = "session", "Session running — no audio"
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
        self.core.inbox.wake()
        self.tray.showMessage("Session ended", "Saved content will be processed in the background.", self.icons["idle"], 3000)
        self._refresh_tray()

    def open_ui(self, path: str = "/") -> None:
        if self.overlay.isVisible():
            self.overlay.raise_()
            self.overlay.activateWindow()
        else:
            self._toggle_overlay({})

    def open_settings(self, tab=None):
        from .settings import SettingsDialog
        dialog = SettingsDialog(self._rebind, self.overlay)
        if tab:
            for index in range(dialog.tabs.count()):
                if dialog.tabs.tabText(index) == tab:
                    dialog.tabs.setCurrentIndex(index)
        dialog.exec()
        self.core.transcriber.wake()
        active = self.core.sessions.active
        self.overlay.project_id = active.project_id if active else get_settings().last_project_id
        self.overlay._load_projects()
        self.overlay.refresh()
        if hasattr(self, "tray"):
            self._refresh_tray()


    def quit(self) -> None:
        if self._quitting:
            return
        if self.core.sessions.active is not None:
            from .glass import confirm
            if not confirm(self.overlay, "Quit Checkpoint",
                           "A session is running. End the session and quit?\n\nEverything captured so far is saved."):
                return
        self._quitting = True
        self.shutdown()
        self.app.quit()

    def shutdown(self) -> None:
        self.capture_button.clear()
        self.held_timer.stop()
        if self._press_recording:
            self._press_recording.cancel()
        for payload, recording in self._held_jobs:
            recording.stop()
            if recording.thread:
                recording.thread.join(2)
            if recording.audio and not recording.retained_note_id:
                from ..services.voice import retain
                retain(payload['id'], recording.audio, recording.text)
        try:
            if self.hotkeys:
                self.hotkeys.stop()
        finally:
            self.core.shutdown()
            self.local_server.close()

    # ------------------------------------------------------------ run
    def run(self) -> int:
        from .hotkeys import HotkeyThread

        self._listen()
        self.core.start()
        self._install_hooks()

        def bindings() -> dict[str, str]:
            hk = get_settings().hotkeys
            return {"capture": hk.capture, **({"review": hk.review} if hk.review else {})}

        self.hotkeys = HotkeyThread(bindings, self._on_hotkey)
        self.hotkeys.start()
        summary = self.hotkeys.summary()
        self._build_tray()
        if not summary["ok"]:
            msg = summary.get("reason") or "Some shortcuts couldn't be registered."
            notices.push("warning", f"Shortcut problem: {msg}. Change them in Settings → Shortcuts.", "hotkeys")
            self.tray.showMessage("Shortcut conflict", msg[:200], QSystemTrayIcon.MessageIcon.Warning, 8000)
        self.app.aboutToQuit.connect(lambda: None if self._quitting else self.shutdown())
        log.info("Desktop host running with native popups")
        print("Checkpoint is running in the tray. Triple tap the capture key to review; hold it to dictate a note.")
        if self.show_panel:
            self.open_ui()
        return self.app.exec()


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    app = QApplication.instance() or QApplication(sys.argv)
    if DesktopHost.signal_existing():
        print("Checkpoint is already running — opened the existing window.")
        return 0
    host = DesktopHost(show_panel="--no-browser" not in argv and "--quiet" not in argv)
    return host.run()
