"""A floating screenshot switcher with inline project creation and gesture review."""
import math
from sqlalchemy import select
from PySide6.QtCore import Qt, QTimer, QUrl, QPointF
from PySide6.QtGui import QPixmap, QPainter, QIcon, QPen, QPolygonF, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                              QProgressBar, QPushButton, QVBoxLayout, QMenu)

from ..services.review import get_work_item, list_work_items, update_work_item
from ..db import read_session, write_session
from ..models import Capture, Project, Session, Setting
from ..storage import media_path
from . import colors
from .windows import popup_css
from .glass import center_on_screen, GlassDialog
from .cards import TaskDeck
from .backdrop import capture_backdrop, paint_backdrop


def control_icon(kind):
    image = QPixmap(24, 24)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(colors.current().text, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if kind == 'close':
        painter.drawLine(6, 6, 18, 18)
        painter.drawLine(18, 6, 6, 18)
    else:
        points = []
        for i in range(32):
            angle = i * math.pi / 16
            radius = 10 if i % 4 in (1, 2) else 7.5
            points.append(QPointF(12 + math.cos(angle) * radius, 12 + math.sin(angle) * radius))
        painter.drawPolygon(QPolygonF(points))
        painter.drawEllipse(QPointF(12, 12), 3, 3)
    painter.end()
    return QIcon(image)


class InboxPanel(QFrame):
    def __init__(self, open_ui=None, open_settings=None, recording=None, recording_status=None, processing_status=None):
        super().__init__()
        self.open_ui, self.open_settings, self.recording = open_ui, open_settings, recording
        self.recording_status, self.processing_status = recording_status, processing_status
        self.setObjectName('inbox')
        self.switcher_backdrop = True
        self.project_id = self.selected_id = self.undo_action = None
        self.frame_index = 0
        self.items = []
        self._direction = 0
        self._back_previews = {}
        self._session_signature = None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle('Checkpoint')
        self.resize(620, 800)
        self.setMinimumSize(560, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.project_picker = QComboBox()
        self.project_picker.setEditable(True)
        self.project_picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.project_picker.setAccessibleName('Project. Type a name and press Enter to create.')
        self.project_picker.setToolTip('Choose a project, or type a new name and press Enter')
        self.project_picker.lineEdit().setMaxLength(200)
        self.project_picker.currentIndexChanged.connect(self._project_changed)
        self.project_picker.lineEdit().returnPressed.connect(self._commit_project)
        header.addWidget(self.project_picker, 1)
        self.settings_button = QPushButton()
        self.close_button = QPushButton()
        for button, kind, label, callback in ((self.settings_button, 'settings', 'Settings', self._settings),
                                               (self.close_button, 'close', 'Close', self.hide)):
            button.setIcon(control_icon(kind))
            button.setFixedSize(36, 36)
            button.setStyleSheet('padding: 4px; border-radius: 18px;')
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.clicked.connect(callback)
            header.addWidget(button)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search tasks')
        self.search.setAccessibleName('Search tasks')
        self.search.textChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        self.status_picker = QComboBox()
        for label, status in (('Open', 'open'), ('Done / archived', 'done'), ('Deleted', 'deleted'), ('All', 'all')):
            self.status_picker.addItem(label, status)
        self.status_picker.setAccessibleName('Task status')
        self.status_picker.setFixedWidth(155)
        self.status_picker.currentIndexChanged.connect(self._filter_changed)
        filters.addWidget(self.status_picker)
        self.session_picker = QComboBox()
        self.session_picker.addItem('All sessions', None)
        self.session_picker.addItem('Without recording', '__unrecorded__')
        self.session_picker.setAccessibleName('Session')
        self.session_picker.currentIndexChanged.connect(self._filter_changed)
        self.session_picker.setFixedWidth(155)
        filters.addWidget(self.session_picker)
        self.position = QLabel()
        self.position.setStyleSheet('color: white; font-size: 12px;')
        layout.addLayout(filters)

        self.deck = TaskDeck(self)
        self.deck.navigate.connect(self._step)
        self.deck.decide.connect(self._status)
        self.deck.expand.connect(lambda: self._open(self.selected_id))
        self.deck.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.deck.customContextMenuRequested.connect(self._task_menu)
        layout.addWidget(self.deck, 1)
        self.position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.position)
        self.notification = QFrame()
        notice = QHBoxLayout(self.notification)
        notice.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        notice.addWidget(self.message, 1)
        self.undo_button = QPushButton('Undo')
        self.undo_button.clicked.connect(self._undo)
        notice.addWidget(self.undo_button)
        self.notification.hide()
        layout.addWidget(self.notification)
        self.notice_timer = QTimer(self, interval=8000, singleShot=True, timeout=self._clear_notice)

        footer = QHBoxLayout()
        self.images_button = QPushButton('Open task images')
        self.images_button.setObjectName('primary')
        self.images_button.setAccessibleName('Open unfinished task images to drag into Codex')
        self.images_button.clicked.connect(self._images)
        self.images_button.setMaximumWidth(380)
        footer.addStretch(1)
        footer.addWidget(self.images_button, 1)
        footer.addStretch(1)
        layout.addLayout(footer)
        self.images_hint = QLabel('Unfinished tasks — drag these images into Codex')
        self.images_hint.setWordWrap(True)
        self.images_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.images_hint.setStyleSheet('font-size: 12px; color: white;')
        layout.addWidget(self.images_hint)

        self.activity_dialog = GlassDialog(self)
        self.activity_dialog.setWindowTitle('Recording and transcription')
        self.activity_dialog.resize(420, 260)
        details = self.activity_dialog.content_layout()
        self.audio_status = QLabel()
        self.processing_label = QLabel()
        for label in (self.audio_status, self.processing_label):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            details.addWidget(label)
        self.processing_progress = QProgressBar()
        self.processing_progress.setTextVisible(False)
        details.addWidget(self.processing_progress)
        self.timer = QTimer(self, interval=1000, timeout=self.refresh)

    def _notify(self, text, undo=False):
        self.message.setText(text)
        self.undo_button.setVisible(undo)
        self.notification.show()
        self.notice_timer.start()

    def _clear_notice(self):
        self.message.clear()
        self.notification.hide()

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
        self.project_id = recording.get('project_id') if recording.get('active') else get_settings().last_project_id
        self.setStyleSheet(popup_css(colors.current()))
        self.settings_button.setIcon(control_icon('settings'))
        self.close_button.setIcon(control_icon('close'))
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
        self.project_picker.addItem('New project…', '__new__')
        index = self.project_picker.findData(self.project_id)
        self.project_picker.setCurrentIndex(index if index >= 0 else 0)
        self.project_picker.blockSignals(False)
        if self.project_id != self.project_picker.currentData():
            self._project_changed()

    def _project_changed(self):
        pid = self.project_picker.currentData()
        if pid == '__new__':
            self.project_picker.setEditText('')
            self.project_picker.lineEdit().setPlaceholderText('New project name')
            self.project_picker.lineEdit().setFocus()
            return
        if not pid:
            return
        from ..settings_store import update_settings
        self.project_id = pid
        self.selected_id = self.undo_action = None
        self._session_signature = None
        self.session_picker.blockSignals(True)
        self.session_picker.setCurrentIndex(0)
        self.session_picker.blockSignals(False)
        self._clear_notice()
        update_settings({'last_project_id': pid})
        self.refresh()

    def _commit_project(self):
        name = self.project_picker.currentText().strip()
        if not name or name == 'New project…':
            return
        with write_session() as session:
            existing = session.scalars(select(Project).where(Project.archived.is_(False), Project.is_demo.is_(False))).all()
            project = next((p for p in existing if p.name.casefold() == name.casefold()), None)
            if project is None:
                project = Project(name=name)
                session.add(project)
                session.flush()
            pid = project.id
        self.project_id = pid
        self._load_projects()
        self._project_changed()
        self.project_picker.lineEdit().clearFocus()
        self.deck.setFocus()

    def _load_sessions(self):
        with read_session() as session:
            rows = list(session.scalars(select(Session).where(Session.project_id == self.project_id).order_by(Session.started_at.desc())).all())
            entries = [(row.id, f"{row.title or 'Recording'} · {row.started_at.astimezone().strftime('%d %b %H:%M')}") for row in rows]
        signature = (self.project_id, tuple(entries))
        if signature == self._session_signature:
            return
        chosen = self.session_picker.currentData()
        self.session_picker.blockSignals(True)
        self.session_picker.clear()
        self.session_picker.addItem('All sessions', None)
        self.session_picker.addItem('Without recording', '__unrecorded__')
        for sid, title in entries:
            self.session_picker.addItem(title, sid)
            self.session_picker.setItemData(self.session_picker.count() - 1, title, Qt.ItemDataRole.ToolTipRole)
        self.session_picker.setCurrentIndex(max(0, self.session_picker.findData(chosen)))
        self.session_picker.blockSignals(False)
        self._session_signature = signature

    def _filter_changed(self):
        self.selected_id = None
        self.refresh()

    def _images(self):
        from ..services.image_cards import refresh, output_folder
        if not self.project_id:
            return
        try:
            refresh()
            status = self.status_picker.currentData()
            folder = output_folder(self.project_id, status if status in ('done', 'deleted') else 'open')
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                self._notify(f'Images folder: {folder}')
        except (OSError, ValueError) as error:
            self._notify(str(error))

    def toggle(self, project_id, work=None):
        if self.isVisible():
            self.hide()
            return
        if self.project_id != project_id:
            self.selected_id = self.undo_action = None
            self._session_signature = None
            self._clear_notice()
        self.project_id = project_id
        self.timer.stop()
        self._load_projects()
        self.setStyleSheet(popup_css(colors.current()))
        self.refresh()
        center_on_screen(self, work)
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.availableGeometry().adjusted(12, 12, -12, -12))
        self.layout().activate()
        self.backdrop = capture_backdrop(self)
        self.show()
        self.deck.setFocus()
        self.raise_()
        self.activateWindow()
        self.timer.start()

    def _pixmap(self, item):
        evidence = next((e for e in item.get('evidence', []) if e['kind'] == 'capture'), None)
        if not evidence:
            return QPixmap()
        with read_session() as session:
            capture = session.get(Capture, evidence['capture_id'])
            path = media_path(capture.image_rel_path) if capture else None
        if not path:
            return QPixmap()
        try:
            stat = path.stat()
        except OSError:
            return QPixmap()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in self._back_previews:
            self._back_previews[key] = QPixmap(str(path))
        return self._back_previews[key]

    def refresh(self):
        if not self.isVisible() and self.timer.isActive():
            self.timer.stop()
            return
        # Polling must not interrupt a swipe or replace the card under the pointer.
        if self.deck.press is not None:
            return
        previous_ids = [item['id'] for item in self.items]
        previous_index = previous_ids.index(self.selected_id) if self.selected_id in previous_ids else 0
        self._load_sessions()
        status = self.status_picker.currentData()
        statuses = {'open': ['open', 'in_progress'], 'done': ['done', 'wont_do'], 'deleted': ['deleted'],
                    'all': ['open', 'in_progress', 'done', 'wont_do', 'deleted']}[status]
        self.items = list(reversed(list_work_items(self.project_id, q=self.search.text(), statuses=statuses,
                                     session_id=self.session_picker.currentData()))) if self.project_id else []
        recording = self.recording_status() if self.recording_status else {}
        self.project_picker.setEnabled(not recording.get('active'))
        if recording.get('active'):
            seconds = int(recording.get('offset_ms', 0) / 1000)
            sources = ', '.join(f"{source.get('label', source.get('kind', 'Audio'))}: {source.get('state', 'starting')}" for source in recording.get('sources', []))
            self.audio_status.setText(f"{'Paused' if recording.get('paused') else 'Recording'} · {seconds // 60:02d}:{seconds % 60:02d} · {sources}")
        else:
            self.audio_status.setText('Audio recording is off')
        self._refresh_processing(recording)
        self._update_loading_card()
        self.session_picker.setToolTip(self.session_picker.currentText())
        self.images_button.setEnabled(bool(self.project_id))
        self.images_button.setText({'done': 'Open done images', 'deleted': 'Open deleted images'}.get(status, 'Open task images'))
        self.images_hint.setText({'done': 'Finished tasks — stored separately from unfinished tasks',
                                 'deleted': 'Deleted tasks — kept here so you can recover them'}.get(status, 'Unfinished tasks — drag these images into Codex'))
        ids = [item['id'] for item in self.items]
        if self.selected_id not in ids:
            remembered = self._saved_selection() if self.selected_id is None else None
            if remembered in ids:
                self.selected_id = remembered
            elif ids:
                self.selected_id = ids[min(previous_index, len(ids) - 1)] if previous_ids and self.selected_id else ids[-1]
            else:
                self.selected_id = None
            self.frame_index = 0
        index = ids.index(self.selected_id) if ids else 0
        self.position.setText(f'{len(ids)} cards' if ids else '')
        item = get_work_item(self.selected_id) if ids else None
        behind = []
        for relative in (-4, -3, -2, -1, 1, 2, 3, 4):
            neighbour = index + relative
            if 0 <= neighbour < len(self.items):
                full = get_work_item(self.items[neighbour]['id'])
                behind.append((relative, full, self._pixmap(full)))
        self.deck.present(item, self._pixmap(item) if item else QPixmap(), len(ids), self._direction, behind, ordinal=index + 1 if ids else 0)
        self._direction = 0
        if len(self._back_previews) > 24:
            self._back_previews.clear()
        self.update_glass_mask()

    def _position_key(self):
        return f"{self.project_id}:{self.status_picker.currentData()}:{self.session_picker.currentData()}"

    def _saved_selection(self):
        if self.search.text().strip():
            return None
        with read_session() as session:
            row = session.get(Setting, 'review_positions')
            return (row.value or {}).get(self._position_key()) if row else None

    def _remember_selection(self):
        if not self.project_id or not self.selected_id or self.search.text().strip():
            return
        with write_session() as session:
            row = session.get(Setting, 'review_positions')
            positions = dict(row.value or {}) if row else {}
            positions[self._position_key()] = self.selected_id
            if row:
                row.value = positions
            else:
                session.add(Setting(key='review_positions', value=positions))

    def update_glass_mask(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_backdrop(painter, self)
        painter.end()

    def _update_loading_card(self):
        from ..transcription.worker import transcription_status
        message = ''
        session_id = self.session_picker.currentData()
        if (self.project_id and self.status_picker.currentData() in ('open', 'all')
                and session_id != '__unrecorded__' and not self.search.text().strip()):
            counts = transcription_status(session_id, project_id=self.project_id)
            worker = self.processing_status() if self.processing_status else {}
            if not worker.get('error') and worker.get('model_installed', True):
                if counts.get('running'):
                    message = 'Adding transcript context…'
                elif counts.get('queued') and not worker.get('paused'):
                    message = 'Waiting for transcription…'
        self.deck.set_processing(message)

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
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 65:
            handle = self.windowHandle()
            if handle:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def _step(self, delta):
        if not self.items:
            return
        index = next((i for i, item in enumerate(self.items) if item['id'] == self.selected_id), 0)
        target = max(0, min(len(self.items) - 1, index + delta))
        self._direction = target - index
        self.selected_id = self.items[target]['id']
        self.frame_index = 0
        self.refresh()
        self._remember_selection()

    def _status(self, value):
        if not self.selected_id:
            return
        try:
            item = get_work_item(self.selected_id)
            if item['work_status'] == value:
                return
            updated = update_work_item(item['id'], {'work_status': value}, item['version'])
            self.undo_action = (item['id'], item['work_status'], updated['version'])
            self._notify({'done': 'Moved to Done.', 'deleted': 'Moved to Deleted.', 'wont_do': 'Archived.', 'open': 'Moved to Open.'}[value], undo=True)
            self.refresh()
        except ValueError as error:
            self._notify(str(error))

    def _undo(self):
        if not self.undo_action:
            return
        item_id, status, version = self.undo_action
        try:
            update_work_item(item_id, {'work_status': status}, version)
            self.undo_action = None
            self.selected_id = item_id
            self._notify('Action undone.')
            self.refresh()
        except ValueError as error:
            self._notify(str(error))

    def _task_menu(self, point):
        if not self.selected_id or not self.deck.card_rect().contains(point):
            return
        menu = QMenu(self)
        menu.addAction('Edit', lambda: self._open(self.selected_id))
        menu.addAction('Mark done', lambda: self._status('done'))
        menu.addAction('Reopen', lambda: self._status('open'))
        menu.addAction('Move to Deleted', lambda: self._status('deleted'))
        menu.exec(self.deck.mapToGlobal(point))

    def hideEvent(self, event):
        self._remember_selection()
        self.timer.stop()
        self.notice_timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Z:
            self._undo()
        elif event.key() == Qt.Key.Key_Delete:
            self._status('deleted')
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Left):
            self._step(1 if event.key() == Qt.Key.Key_Right else -1)
        else:
            super().keyPressEvent(event)
