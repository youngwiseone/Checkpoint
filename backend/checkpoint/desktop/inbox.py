"""Native capture hub with review, settings and editing popups."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QVBoxLayout

from ..services.review import get_work_item, list_work_items, update_work_item
from ..db import read_session
from ..models import Capture
from ..storage import media_path
from . import colors
from .windows import popup_css
from .glass import center_on_screen, get_text
from .cards import TaskDeck
from .backdrop import capture_backdrop, paint_backdrop


class InboxPanel(QFrame):
    def __init__(self, open_ui=None, open_settings=None, recording=None, recording_status=None, processing_status=None):
        super().__init__()
        self.open_ui = open_ui
        self.open_settings = open_settings
        self.recording = recording
        self.recording_status = recording_status
        self.processing_status = processing_status
        self.setObjectName("inbox")
        self.project_id = None
        self.selected_id = None
        self.frame_index = 0
        self.items = []
        self.undo_action = None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Checkpoint inbox")
        self.resize(460, 800)
        self.setMinimumSize(360, 600)
        self._direction = 0
        self._image_key = None
        self._preview = QPixmap()
        self._back_previews = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        header_pill = QFrame()
        header_pill.setObjectName("controlPill")
        header = QHBoxLayout(header_pill)
        header.setContentsMargins(14, 8, 10, 8)
        heading = QLabel("Checkpoint")
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        header.addWidget(heading)
        self.position = QLabel("Current tasks")
        header.addWidget(self.position, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addWidget(header_pill)
        filters = QHBoxLayout()
        self.project_picker = QComboBox()
        self.project_picker.currentIndexChanged.connect(self._project_changed)
        filters.addWidget(self.project_picker, 1)
        self.status_picker = QComboBox()
        for label, status in (("Open", "open"), ("Done / archived", "done"), ("All", "all")):
            self.status_picker.addItem(label, status)
        self.status_picker.addItem("Trash", "deleted")
        self.status_picker.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status_picker)
        layout.addLayout(filters)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tasks and notes")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.deck = TaskDeck(self)
        self.deck.navigate.connect(self._step)
        self.deck.decide.connect(self._status)
        self.deck.expand.connect(lambda: self._open(self.selected_id))
        layout.addWidget(self.deck, 1)
        actions = QHBoxLayout()
        for label, callback in (("Previous", lambda: self._step(-1)), ("Next", lambda: self._step(1)),
                                ("Edit", lambda: self._open(self.selected_id)),
                                ("Done", self._complete),
                                ("Delete", lambda: self._status("deleted")), ("Undo", self._undo)):
            button = QPushButton({"Previous": "↑", "Next": "↓"}.get(label, label))
            button.setAccessibleName(label)
            button.setToolTip(label + (" task" if label in ("Previous", "Next") else ""))
            button.clicked.connect(callback)
            actions.addWidget(button)
            setattr(self, label.lower() + "_button", button)
        layout.addLayout(actions)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        activity = QFrame()
        activity.setObjectName("activityCard")
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(14, 10, 14, 10)
        activity_layout.setSpacing(5)
        activity_heading = QLabel("Live activity")
        activity_heading.setObjectName("title")
        activity_layout.addWidget(activity_heading)
        self.audio_status = QLabel()
        self.audio_status.setWordWrap(True)
        self.audio_status.setTextFormat(Qt.TextFormat.PlainText)
        activity_layout.addWidget(self.audio_status)
        self.processing_label = QLabel()
        self.processing_label.setWordWrap(True)
        self.processing_label.setTextFormat(Qt.TextFormat.PlainText)
        activity_layout.addWidget(self.processing_label)
        self.processing_progress = QProgressBar()
        self.processing_progress.setTextVisible(False)
        activity_layout.addWidget(self.processing_progress)
        layout.addWidget(activity)
        bottom = QHBoxLayout()
        for label, callback in (("Add note", self._add_note), ("History", self._history),
                                ("Settings", self._settings)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            bottom.addWidget(button)
        self.record_button = QPushButton("Start recording")
        self.record_button.clicked.connect(self._record)
        bottom.addWidget(self.record_button)
        layout.addLayout(bottom)
        self.timer = QTimer(self, interval=1000, timeout=self.refresh)

    def _open(self, item_id=None):
        if item_id:
            from .editor import TaskDialog
            self.timer.stop()
            TaskDialog(item_id, self).exec()
            self.refresh()
            if self.isVisible():
                self.timer.start()

    def _settings(self):
        from .settings import SettingsDialog
        self.timer.stop()
        if self.open_settings:
            self.open_settings()
        else:
            SettingsDialog(parent=self).exec()
        from ..settings_store import get_settings
        recording = self.recording_status() if self.recording_status else {}
        self.project_id = recording.get("project_id") if recording.get("active") else get_settings().last_project_id
        c = colors.current()
        self.setStyleSheet(popup_css(c))
        self._load_projects()
        self.refresh()
        if self.isVisible():
            self.timer.start()

    def _load_projects(self):
        from .settings import projects
        self.project_picker.blockSignals(True)
        self.project_picker.clear()
        for pid, name in projects():
            self.project_picker.addItem(name, pid)
        index = self.project_picker.findData(self.project_id)
        self.project_picker.setCurrentIndex(index if index >= 0 else 0)
        self.project_picker.blockSignals(False)
        if self.project_id != self.project_picker.currentData():
            self._project_changed()

    def _project_changed(self):
        from ..settings_store import update_settings
        self.project_id = self.project_picker.currentData()
        self.selected_id, self.undo_action = None, None
        update_settings({"last_project_id": self.project_id})
        self.refresh()

    def _add_note(self):
        from ..services.review import create_work_item
        if not self.project_id:
            self._settings()
            return
        text, ok = get_text(self, "Add note", "What should we remember?", multiline=True)
        if ok and text.strip():
            item = create_work_item(self.project_id, "note", text.strip().splitlines()[0][:300], text.strip())
            self.status_picker.setCurrentIndex(0)
            self.selected_id = item["id"]
            self.refresh()

    def _history(self):
        from .history import HistoryDialog
        self.timer.stop()
        HistoryDialog(self.project_id, self).exec()
        self.refresh()
        if self.isVisible():
            self.timer.start()

    def _record(self):
        if self.recording:
            self.recording()
            self.refresh()

    def toggle(self, project_id, work=None):
        if self.isVisible():
            self.hide()
            return
        if self.project_id != project_id:
            self.selected_id, self.undo_action = None, None
            self.message.clear()
        self.project_id = project_id
        self.timer.stop()
        self._load_projects()
        c = colors.current()
        self.setStyleSheet(popup_css(c))
        self.refresh()
        self.resize(460, 800)
        center_on_screen(self, work)
        self.layout().activate()
        self.backdrop = capture_backdrop(self)
        self.show()
        self.deck.setFocus()
        self.raise_()
        self.activateWindow()
        self.timer.start()

    def refresh(self):
        if not self.isVisible() and self.timer.isActive():
            self.timer.stop()
            return
        previous_ids = [item["id"] for item in self.items]
        previous_index = previous_ids.index(self.selected_id) if self.selected_id in previous_ids else 0
        status = self.status_picker.currentData()
        statuses = {"open": ["open", "in_progress"], "done": ["done", "wont_do"], "deleted": ["deleted"]}.get(status)
        self.items = list_work_items(self.project_id, q=self.search.text(), statuses=statuses) if self.project_id else []
        recording = self.recording_status() if self.recording_status else {}
        self.record_button.setVisible(self.recording is not None)
        self.record_button.setText("Stop recording" if recording.get("active") else "Start recording")
        self.project_picker.setEnabled(not recording.get("active"))
        if recording.get("active"):
            sources = recording.get("sources", [])
            text = "Paused" if recording.get("paused") else "Recording"
            details = ", ".join(f"{source.get('label', source.get('kind', 'Audio'))}: {source.get('state', 'starting')}" for source in sources) or "no audio source enabled"
            seconds = int(recording.get("offset_ms", 0) / 1000)
            self.audio_status.setText(f"{text} · {seconds // 60:02d}:{seconds % 60:02d} · {details}")
        else:
            self.audio_status.setText("Audio recording is off")
        self._refresh_processing(recording)
        ids = [item["id"] for item in self.items]
        if self.selected_id not in ids:
            self.selected_id = ids[min(previous_index, len(ids) - 1)] if ids else None
            self.frame_index = 0
        self.undo_button.setEnabled(self.undo_action is not None)
        for button in (self.edit_button, self.done_button, self.delete_button):
            button.setEnabled(bool(ids))
        index = ids.index(self.selected_id) if ids else 0
        self.previous_button.setEnabled(bool(ids) and index > 0)
        self.next_button.setEnabled(bool(ids) and index < len(ids) - 1)
        self.position.setText(f"Task {index + 1} of {len(ids)}" if ids else "Current tasks")
        item = get_work_item(self.selected_id) if ids else None
        pixmap = QPixmap()
        if item:
            self.done_button.setText("Restore" if item["work_status"] == "deleted" else "Reopen" if item["work_status"] in ("done", "wont_do") else "Approve")
            captures = [e for e in item["evidence"] if e["kind"] == "capture"]
            if captures:
                self.frame_index = min(self.frame_index, len(captures) - 1)
                with read_session() as session:
                    capture = session.get(Capture, captures[self.frame_index]["capture_id"])
                    path = media_path(capture.image_rel_path) if capture else None
                if path:
                    try:
                        stat = path.stat()
                        key = (str(path), stat.st_mtime_ns, stat.st_size)
                    except OSError:
                        key = None
                    if key != self._image_key:
                        self._image_key = key
                        self._preview = QPixmap(str(path))
                    pixmap = self._preview if key else QPixmap()
        behind = []
        for back_item in (self.items[index + 1:] + self.items[:index])[:2]:
            full = get_work_item(back_item["id"])
            back_image = QPixmap()
            evidence = next((e for e in full["evidence"] if e["kind"] == "capture"), None)
            if evidence:
                with read_session() as session:
                    capture = session.get(Capture, evidence["capture_id"])
                    path = media_path(capture.image_rel_path) if capture else None
                if path and path.exists():
                    stat = path.stat()
                    key = (str(path), stat.st_mtime_ns, stat.st_size)
                    if key not in self._back_previews:
                        self._back_previews[key] = QPixmap(str(path))
                    back_image = self._back_previews[key]
            behind.append((full, back_image))
        if len(self._back_previews) > 6:
            self._back_previews.clear()
        self.deck.present(item, pixmap, len(ids), self._direction, behind)
        self._direction = 0
        self.message.setVisible(bool(self.message.text()))
        if self.isVisible():
            self.update_glass_mask()

    def update_glass_mask(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_backdrop(painter, self)
        painter.end()


    def _refresh_processing(self, recording):
        from ..transcription.worker import transcription_status
        counts = transcription_status(recording.get("session_id"))
        worker = self.processing_status() if self.processing_status else {}
        done, queued, running, failed = (counts.get(k, 0) for k in ("done", "queued", "running", "failed"))
        total = done + queued + running + failed
        state = "Transcribing audio" if running else "Waiting to transcribe" if queued else "Some audio clips failed" if failed else "Transcript up to date" if done else "No audio clips to process"
        if worker.get("paused"):
            state = "Transcription paused"
        elif worker.get("error"):
            state = "Transcription needs attention"
        elif total and not worker.get("model_installed", True):
            state = "Download a transcript model in Settings"
        summary = f"{state} · {done} of {total} clips complete · {queued} waiting"
        if failed:
            summary += f" · {failed} failed"
        if not total:
            summary = state
        elif not (queued or running or failed or recording.get("active") or worker.get("paused") or worker.get("error")):
            summary = state + " · All audio clips processed"
        if worker.get("error"):
            summary += f"\n{worker['error']}"
        self.processing_label.setText(summary)
        self.processing_progress.setRange(0, max(total, 1))
        self.processing_progress.setValue(done)
        self.processing_progress.setVisible(bool(total and (queued or running or failed or recording.get("active"))))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 70:
            handle = self.windowHandle()
            if handle:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def _done(self, item_id):
        self.selected_id = item_id
        self._status("done")

    def _complete(self):
        item = next((item for item in self.items if item["id"] == self.selected_id), None)
        if item:
            self._status("open" if item["work_status"] in ("done", "wont_do", "deleted") else "done")

    def _step(self, delta):
        if not self.items:
            return
        index = next((i for i, item in enumerate(self.items) if item["id"] == self.selected_id), 0)
        self._direction = delta
        self.selected_id = self.items[max(0, min(len(self.items) - 1, index + delta))]["id"]
        self.frame_index = 0
        self.refresh()

    def _frame(self, index):
        self.frame_index = index
        self.refresh()

    def _status(self, value):
        if not self.selected_id:
            return
        try:
            item = get_work_item(self.selected_id)
            updated = update_work_item(item["id"], {"work_status": value}, item["version"])
            self.undo_action = (item["id"], item["work_status"], updated["version"])
            self._direction = 1 if value in ("done", "deleted") else 0
            self.message.setText({"done": "Task approved. Undo to restore.", "deleted": "Task deleted. Undo to restore.", "wont_do": "Task archived.", "open": "Task reopened."}[value])
            self.refresh()
        except ValueError as error:
            self.message.setText(str(error))

    def _undo(self):
        if not self.undo_action:
            return
        item_id, status, version = self.undo_action
        try:
            update_work_item(item_id, {"work_status": status}, version)
            self.undo_action = None
            self.selected_id = item_id
            self.message.setText("Action undone.")
            self.refresh()
        except ValueError as error:
            self.message.setText(str(error))

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._step(1 if event.key() == Qt.Key.Key_Down else -1)
        else:
            super().keyPressEvent(event)
