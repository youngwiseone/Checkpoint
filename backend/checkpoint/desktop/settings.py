"""Native settings; no HTTP server or browser is needed."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialogButtonBox,
                              QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QTabWidget, QWidget)
from sqlalchemy import select

from ..db import read_session, write_session
from ..models import Project
from ..settings_store import get_settings, update_settings
from .windows import popup_css
from . import colors
from .glass import GlassDialog, get_directory, get_text


def projects():
    with read_session() as s:
        return [(p.id, p.name) for p in s.scalars(select(Project).where(Project.archived.is_(False), Project.is_demo.is_(False)).order_by(Project.name))]


def combo(options, selected):
    box = QComboBox()
    for label, value in options:
        box.addItem(label, value)
    index = box.findData(selected)
    if index >= 0:
        box.setCurrentIndex(index)
    return box


class SettingsDialog(GlassDialog):
    def __init__(self, rebind=lambda: {"ok": True}, parent=None):
        super().__init__(parent)
        self.rebind = rebind
        self.setWindowTitle("Settings")
        self.resize(720, 560)
        self.setStyleSheet(popup_css(colors.current()))
        st = get_settings()
        layout = self.content_layout()
        tabs = self.tabs = QTabWidget()
        tabs.tabBar().setExpanding(False)
        layout.addWidget(tabs)

        def tab(name):
            widget = QWidget()
            widget.setObjectName('settingsPage')
            form = QFormLayout(widget)
            form.setContentsMargins(18, 22, 18, 22)
            form.setVerticalSpacing(18)
            form.setHorizontalSpacing(20)
            tabs.addTab(widget, name)
            return form

        general = tab("General")
        self.project = combo([(name, pid) for pid, name in projects()], st.last_project_id)
        general.addRow("Project", self.project)
        project_actions = QHBoxLayout()
        for label, callback in (("Add project", self._add_project), ("Rename", self._rename_project)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            project_actions.addWidget(button)
        general.addRow(project_actions)
        self.capture_key = QLineEdit(st.hotkeys.capture)
        general.addRow("Capture key", self.capture_key)
        hint = QLabel("Tap: screenshot · Double tap: add text to screenshot · Hold: menu")
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        general.addRow(hint)
        self.region = combo([("Active display", "foreground_monitor"), ("Active window", "foreground_window")], st.capture.region)
        general.addRow("Screenshot area", self.region)

        audio = tab("Audio")
        from ..audio.devices import list_devices
        try:
            devices = list_devices()
        except Exception as error:
            devices = {"mic": [], "loopback": [], "error": str(error)}
        self.mic = QCheckBox("Record microphone")
        self.mic.setChecked(st.audio.mic_enabled)
        self.loopback = QCheckBox("Record computer audio")
        self.loopback.setChecked(st.audio.loopback_enabled)
        self.mic_device = combo([("System default", None), *[(d["name"], d["name"]) for d in devices["mic"]]], st.audio.mic_device)
        self.loop_device = combo([("System default", None), *[(d["name"], d["name"]) for d in devices["loopback"]]], st.audio.loopback_device)
        # Keep a saved unplugged device selectable instead of silently switching it.
        for box, saved in ((self.mic_device, st.audio.mic_device), (self.loop_device, st.audio.loopback_device)):
            if saved and box.findData(saved) < 0:
                box.addItem(saved + " (unavailable)", saved)
                box.setCurrentIndex(box.count() - 1)
        audio.addRow(self.mic)
        audio.addRow("Microphone", self.mic_device)
        audio.addRow(self.loopback)
        audio.addRow("Computer audio", self.loop_device)
        audio_hint = QLabel("Saved choices apply to the next recording.")
        audio_hint.setObjectName('muted')
        audio.addRow(audio_hint)
        if devices.get("error"):
            warning = QLabel(devices["error"])
            warning.setWordWrap(True)
            audio.addRow(warning)

        transcription = tab("Transcript")
        from ..transcription.engine import MODEL_CHOICES
        self.model = combo([(f"{name} (~{info['approx_mb']} MB)", name) for name, info in MODEL_CHOICES.items()], st.transcription.model)
        transcription.addRow("Local model", self.model)
        self.model_status = QLabel()
        self.model_status.setObjectName('muted')
        self.model_status.setWordWrap(True)
        transcription.addRow(self.model_status)
        self.download = QPushButton("Download selected model")
        self.download.clicked.connect(self._download)
        transcription.addRow(self.download)
        self.timing = combo([("Live", "live"), ("After recording", "after"), ("Off", "off")], st.transcription.default_mode)
        self.transcribe = QCheckBox("Background transcription enabled")
        self.transcribe.setChecked(not st.transcription.paused)
        transcription.addRow("Timing", self.timing)
        transcription.addRow(QLabel("Whistle runs locally on the CPU. Audio stays on this PC."))
        transcription.addRow(self.transcribe)
        self.model.currentIndexChanged.connect(self._model_status)
        self.timer = QTimer(self, interval=1000, timeout=self._model_status)
        self.timer.start()
        self._model_status()

        chat = tab("Chat")
        from .. import integrations
        for item in integrations.status():
            button = QPushButton("Disconnect" if item["connected"] else "Connect")
            button.setEnabled(item["installed"] or item["connected"])
            button.clicked.connect(lambda checked=False, app=item["app"], b=button: self._connect(app, b))
            chat.addRow(item["name"], button)
        text = QLabel('Ask your chat: “Show my latest Checkpoint captures”.\nRestart Codex or Claude after connecting. Tasks and screenshots are read-only in chat.')
        text.setWordWrap(True)
        chat.addRow(text)

        storage = tab("Storage")
        from ..config import paths
        folder = QLineEdit(str(paths().root))
        folder.setReadOnly(True)
        storage.addRow("Data folder", folder)
        storage_hint = QLabel("Your screenshots, audio and transcripts stay on this PC.")
        storage_hint.setObjectName('muted')
        storage_hint.setWordWrap(True)
        storage.addRow(storage_hint)
        choose = QPushButton("Choose data folder for next launch")
        choose.clicked.connect(self._storage)
        storage.addRow(choose)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('primary')
        layout.addWidget(buttons)

    def _add_project(self):
        name, ok = get_text(self, "Add project", "Project name")
        if not ok or not name.strip():
            return
        with write_session() as s:
            p = Project(name=name.strip()[:200])
            s.add(p)
            s.flush()
            pid = p.id
        self.project.addItem(name.strip()[:200], pid)
        self.project.setCurrentIndex(self.project.count() - 1)

    def _rename_project(self):
        pid = self.project.currentData()
        if not pid:
            return
        name, ok = get_text(self, "Rename project", "Project name", text=self.project.currentText())
        if ok and name.strip():
            with write_session() as s:
                s.get(Project, pid).name = name.strip()[:200]
            self.project.setItemText(self.project.currentIndex(), name.strip()[:200])

    def _model_status(self):
        from ..transcription.engine import downloads, model_installed
        name = self.model.currentData()
        status = downloads.status().get(name, {})
        downloading = status.get("state") == "downloading"
        installed = model_installed(name)
        text = "Installed · works offline" if installed else "Not installed · download once to enable transcription"
        if downloading:
            text = f"Downloading: {status.get('bytes_done', 0) / 1e6:.0f} / {status.get('bytes_total', 0) / 1e6:.0f} MB"
        elif status.get("error"):
            text = status["error"]
        self.model_status.setText(text)
        self.download.setEnabled(not downloading and not installed)

    def _download(self):
        from ..transcription.engine import downloads
        downloads.start(self.model.currentData())
        self._model_status()

    def _connect(self, app, button):
        from .. import integrations
        try:
            status = next(i for i in integrations.status() if i["app"] == app)
            result = integrations.set_connected(app, not status["connected"])
            button.setText("Disconnect" if result["connected"] else "Connect")
            self.message.setText("Chat connection updated. Fully quit and restart that app to apply.")
        except Exception as error:
            self.message.setText(str(error))

    def _storage(self):
        from pathlib import Path
        from ..config import set_data_dir_pointer
        folder = get_directory(self, "Data folder for next launch")
        if folder:
            try:
                set_data_dir_pointer(Path(folder))
                self.message.setText("Location saved for next launch. Copy your existing data there before restarting; files are not moved automatically.")
            except OSError as error:
                self.message.setText(str(error))

    def _save(self):
        from .hotkeys import parse
        try:
            capture = self.capture_key.text().strip()
            review = ""
            capture_binding = parse(capture)
            if review and parse(review) == capture_binding:
                raise ValueError("Capture and review must use different keys. Leave review empty to use only one key.")
            update_settings({"last_project_id": self.project.currentData(), "first_run_complete": True,
                             "hotkeys": {"capture": capture, "review": review},
                             "capture": {"region": self.region.currentData()},
                             "audio": {"mic_enabled": self.mic.isChecked(), "mic_device": self.mic_device.currentData(),
                                       "loopback_enabled": self.loopback.isChecked(), "loopback_device": self.loop_device.currentData()},
                             "transcription": {"model": self.model.currentData(), "device": "cpu",
                                               "default_mode": self.timing.currentData(),
                                               "paused": not self.transcribe.isChecked()}})
            result = self.rebind()
            if not result.get("ok", True):
                self.message.setText("Saved, but a shortcut could not be registered: " + (result.get("reason") or "Try another key."))
                return
            self.accept()
        except ValueError as error:
            self.message.setText(str(error))
