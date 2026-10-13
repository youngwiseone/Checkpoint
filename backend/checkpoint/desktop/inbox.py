"""A floating screenshot switcher with inline project creation and gesture review."""
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from contextlib import contextmanager
from sqlalchemy import select
from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal, QSize
from PySide6.QtGui import QImage, QImageReader, QPixmap, QPainter, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                              QProgressBar, QPushButton, QVBoxLayout, QMenu)

from ..services.review import get_work_item, list_work_items, update_work_items
from ..db import read_session, write_session
from ..models import Capture, Project, Session, Setting
from ..storage import media_path
from . import colors
from .windows import popup_css
from .glass import center_on_screen, GlassDialog, get_text, paint_glass
from .icons import control_icon
from .cards import TaskDeck
from .backdrop import capture_backdrop, paint_backdrop




class ProjectPicker(QComboBox):
    rename_requested = Signal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.isEditable():
            self.hidePopup()
            self.rename_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class InboxPanel(QFrame):
    preview_ready = Signal(object, QImage)
    def __init__(self, open_ui=None, open_settings=None, recording=None, recording_status=None, processing_status=None, new_session=None):
        super().__init__()
        self.open_ui, self.open_settings, self.recording = open_ui, open_settings, recording
        self.recording_status, self.processing_status = recording_status, processing_status
        self.new_session = new_session
        self.setObjectName('inbox')
        self.switcher_backdrop = True
        self.project_id = self.selected_id = self.undo_action = None
        self.selection = set()
        self.selection_anchor = None
        self.selection_explicit = False
        self._child_depth = 0
        self.frame_index = 0
        self.items = []
        self._direction = 0
        self._back_previews = OrderedDict()
        self._details = {}
        self._capture_paths = {}
        self._preview_pending = set()
        self._preview_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='previews')
        self.preview_ready.connect(self._preview_loaded)
        self._session_signature = None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle('Checkpoint')
        self.resize(1180, 800)
        self.setMinimumSize(560, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(10)

        self.toolbar = QGridLayout()
        self.toolbar.setSpacing(8)
        self._toolbar_wide = None
        self.project_picker = ProjectPicker()
        self.project_picker.setObjectName('toolbarPicker')
        self.project_picker.setFixedHeight(48)
        self.project_picker.setMinimumWidth(180)
        self.project_picker.setMaximumWidth(350)
        self.project_picker.setStyleSheet('QComboBox { padding: 10px 16px; font-size: 16px; font-weight: 600; border-radius: 24px; }')
        self.project_picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.project_picker.setAccessibleName('Choose project. Double-click to rename.')
        self.project_picker.setToolTip('Click to switch projects. Double-click to rename the current project.')
        self.project_picker.currentIndexChanged.connect(self._project_changed)
        self.project_picker.rename_requested.connect(self._rename_project)
        self.search = QLineEdit()
        self.search.setObjectName('taskSearch')
        self.search.setFixedHeight(48)
        self.search.setMinimumWidth(140)
        self.search.setPlaceholderText('Search tasks')
        self.search.setAccessibleName('Search tasks')
        self.search.addAction(control_icon('search'), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        self.status_picker = QComboBox()
        for label, status in (('Open', 'open'), ('Done / archived', 'done'), ('Deleted', 'deleted'), ('All', 'all')):
            self.status_picker.addItem(label, status)
        self.status_picker.setAccessibleName('Task status')
        self.session_picker = QComboBox()
        self.session_picker.addItem('All sessions', None)
        self.session_picker.addItem('Without recording', '__unrecorded__')
        self.session_picker.setAccessibleName('Session')
        for picker in (self.status_picker, self.session_picker):
            picker.setObjectName('toolbarPicker')
            picker.setFixedSize(156, 48)
            picker.currentIndexChanged.connect(self._filter_changed)
        self.settings_button = QPushButton()
        self.record_button = QPushButton()
        self.close_button = QPushButton()
        self.record_button.setCheckable(True)
        self.record_button.setProperty('recording', False)
        for button, kind, label, callback in (
                (self.settings_button, 'settings', 'Settings', self._settings),
                (self.record_button, 'record', 'Start recording', self._toggle_recording),
                (self.close_button, 'close', 'Close', self.hide)):
            button.setObjectName('recordButton' if kind == 'record' else 'glassCircle')
            button.setIcon(control_icon(kind))
            button.setIconSize(QSize(24, 24))
            button.setFixedSize(44, 44)
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.clicked.connect(callback)
        self.record_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.record_button.customContextMenuRequested.connect(self._record_menu)
        self._record_visual_state = None
        layout.addLayout(self.toolbar)
        self._layout_toolbar()
        self.position = QLabel()
        self.position.setObjectName('muted')

        self.deck = TaskDeck(self)
        self.deck.navigate.connect(self._step)
        self.deck.jump.connect(self._jump)
        self.deck.decide.connect(self._status)
        self.deck.select_cards.connect(self._select_cards)
        self.deck.prepare_group_drag.connect(self._prepare_group_drag)
        self.deck.expand.connect(lambda: self._open(self.selected_id))
        self.deck.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.deck.customContextMenuRequested.connect(self._task_menu)
        layout.addWidget(self.deck, 1)
        self.position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.position.hide()
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
        self.images_button.setIcon(control_icon('images', colors.current().on_accent))
        self.images_button.setIconSize(QSize(24, 24))
        self.images_button.setMinimumHeight(48)
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
        self.images_hint.setObjectName('muted')
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
        self.deactivate_timer = QTimer(self, interval=80, singleShot=True, timeout=self._hide_if_external_focus)
        QApplication.instance().applicationStateChanged.connect(self._application_state_changed)

    def _belongs_to_panel(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _hide_if_external_focus(self):
        if not self.isVisible() or self._child_depth:
            return
        if any(dialog.isWindow() and dialog.isVisible() for dialog in self.findChildren(QDialog)):
            return
        app = QApplication.instance()
        if self._belongs_to_panel(app.activeModalWidget()):
            return
        if self._belongs_to_panel(app.activePopupWidget()):
            self.deactivate_timer.start()
            return
        if (app.applicationState() == Qt.ApplicationState.ApplicationActive
                and self._belongs_to_panel(app.activeWindow())):
            return
        self.hide()

    def _application_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationInactive and self.isVisible():
            self.deactivate_timer.start()

    def changeEvent(self, event):
        if hasattr(self, 'deactivate_timer'):
            if event.type() == QEvent.Type.ActivationChange:
                if self.isActiveWindow():
                    self.deactivate_timer.stop()
                elif self.isVisible():
                    self.deactivate_timer.start()
        super().changeEvent(event)

    @contextmanager
    def _child_window(self):
        self._child_depth += 1
        self.deactivate_timer.stop()
        try:
            yield
        finally:
            self._child_depth -= 1
            if self.isVisible() and not self.isActiveWindow():
                self.deactivate_timer.start()

    def _clear_selection(self):
        self.selection.clear()
        self.selection_anchor = None
        self.selection_explicit = False

    def _select_cards(self, relative, modifiers):
        ids = [item['id'] for item in self.items]
        if not ids:
            return
        index = ids.index(self.selected_id) if self.selected_id in ids else 0
        target_index = max(0, min(len(ids) - 1, index + relative))
        target = ids[target_index]
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            anchor = self.selection_anchor if self.selection_anchor in ids else self.selected_id
            anchor_index = ids.index(anchor) if anchor in ids else index
            chosen = set(ids[min(anchor_index, target_index):max(anchor_index, target_index) + 1])
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                self.selection.update(chosen)
            else:
                self.selection = chosen
            self.selection_anchor = anchor
            self.selection_explicit = True
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            self.selection.symmetric_difference_update({target})
            self.selection_anchor = target
            self.selection_explicit = True
        else:
            self.selection = {target}
            self.selection_anchor = target
            self.selection_explicit = False
        self.selected_id = target
        self._direction = target_index - index
        self._present()
        self._remember_selection()

    def _prepare_group_drag(self, relative):
        ids = [item['id'] for item in self.items]
        if self.selected_id not in ids:
            return
        target = ids[max(0, min(len(ids) - 1, ids.index(self.selected_id) + relative))]
        if target not in self.selection:
            self.selection = {target}
            self.selection_anchor = target
            self.selection_explicit = False
        self._present()

    def _layout_toolbar(self):
        wide = self.width() >= 1000
        if wide == self._toolbar_wide:
            return
        self._toolbar_wide = wide
        while self.toolbar.count():
            self.toolbar.takeAt(0)
        for column in range(7):
            self.toolbar.setColumnStretch(column, 0)
        if wide:
            for column, widget in enumerate((self.project_picker, self.search, self.status_picker,
                                            self.session_picker, self.settings_button,
                                            self.record_button, self.close_button)):
                self.toolbar.addWidget(widget, 0, column)
            self.toolbar.setColumnStretch(0, 2)
            self.toolbar.setColumnStretch(1, 4)
        else:
            self.toolbar.addWidget(self.project_picker, 0, 0, 1, 3)
            for column, widget in enumerate((self.settings_button, self.record_button, self.close_button), 3):
                self.toolbar.addWidget(widget, 0, column)
            self.toolbar.addWidget(self.search, 1, 0, 1, 2)
            self.toolbar.addWidget(self.status_picker, 1, 2, 1, 2)
            self.toolbar.addWidget(self.session_picker, 1, 4, 1, 2)
            self.toolbar.setColumnStretch(0, 1)
        margin = 36 if wide else 22
        self.layout().setContentsMargins(margin, 24, margin, 24)

    def resizeEvent(self, event):
        if hasattr(self, 'toolbar'):
            self._layout_toolbar()
        super().resizeEvent(event)

    def _toggle_recording(self):
        if not self.project_id or self.project_picker.isEditable():
            return
        try:
            if self.recording:
                self.recording()
            elif self.new_session:
                self._new_session()
        except (ValueError, OSError) as error:
            self._notify(str(error))
        finally:
            self.refresh()

    def _record_menu(self, point):
        menu = QMenu(self)
        menu.addAction('Recording and transcription', self.activity_dialog.exec)
        menu.addAction('Transcript history', self._transcript_history)
        menu.addAction('Open transcripts folder', self._transcripts_folder)
        if self.new_session and self.project_id:
            menu.addAction('Start a new session', self._new_session)
        with self._child_window():
            menu.exec(self.record_button.mapToGlobal(point))

    def _transcript_history(self):
        from .history import HistoryDialog
        with self._child_window():
            HistoryDialog(self.project_id, self).exec()
        self.refresh()

    def _transcripts_folder(self):
        from ..config import paths
        folder = paths().transcripts
        self.hide()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self.show()
            self._notify(f'Transcripts folder: {folder}')

    def _refresh_record_button(self, recording):
        active, paused = bool(recording.get('active')), bool(recording.get('paused'))
        self.record_button.setEnabled(bool(self.project_id and (self.recording or self.new_session))
                                      and not self.project_picker.isEditable())
        self.record_button.setChecked(active)
        state = (active, paused, colors.current().name)
        if state != self._record_visual_state:
            self._record_visual_state = state
            self.record_button.setProperty('recording', active and not paused)
            self.record_button.setIcon(control_icon('record', colors.current().warn if paused else None,
                                                    active=active and not paused))
            self.record_button.style().unpolish(self.record_button)
            self.record_button.style().polish(self.record_button)
        label = 'Stop recording' if active else 'Start recording'
        seconds = int(recording.get('offset_ms', 0) / 1000)
        detail = f" · {'Paused' if paused else 'Recording'} {seconds // 60:02d}:{seconds % 60:02d}" if active else ''
        self.record_button.setAccessibleName(label)
        self.record_button.setToolTip(label + detail + '\nRight-click for recording and transcription details.')

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
            with self._child_window():
                TaskDialog(item_id, self).exec()
            self.refresh()
            if self.isVisible():
                self.timer.start()

    def _settings(self):
        from .settings import SettingsDialog
        self.timer.stop()
        previous_project = self.project_id
        with self._child_window():
            if self.open_settings:
                self.open_settings()
            else:
                SettingsDialog(parent=self).exec()
        from ..settings_store import get_settings
        recording = self.recording_status() if self.recording_status else {}
        self.project_id = recording.get('project_id') if recording.get('active') else get_settings().last_project_id
        if previous_project != self.project_id:
            self._clear_selection()
        self.setStyleSheet(popup_css(colors.current()))
        self.settings_button.setIcon(control_icon('settings'))
        self.close_button.setIcon(control_icon('close'))
        self._load_projects()
        self.refresh()
        if self.isVisible():
            self.timer.start()

    def _new_session(self):
        if not self.project_id or self.project_picker.isEditable() or not self.new_session:
            return
        try:
            self.new_session(self.project_id)
        except ValueError as error:
            self._notify(str(error))
            return
        self.session_picker.setCurrentIndex(0)
        self.refresh()
        self._notify('New session started for this project.')

    def _load_projects(self):
        from .settings import projects
        self.project_picker.blockSignals(True)
        self.project_picker.clear()
        for pid, name in projects():
            self.project_picker.addItem(control_icon('folder'), name, pid)
        self.project_picker.addItem('New project…', '__new__')
        index = self.project_picker.findData(self.project_id)
        self.project_picker.setCurrentIndex(index if index >= 0 else 0)
        self.project_picker.blockSignals(False)
        if self.project_id != self.project_picker.currentData():
            self._project_changed()

    def _project_changed(self):
        pid = self.project_picker.currentData()
        if pid == '__new__':
            self.project_picker.setEditable(True)
            self.project_picker.lineEdit().setMaxLength(200)
            self.project_picker.lineEdit().returnPressed.connect(self._commit_project)
            self.project_picker.setEditText('')
            self.project_picker.lineEdit().setPlaceholderText('New project name')
            self.project_picker.lineEdit().setFocus()
            return
        self.project_picker.setEditable(False)
        if not pid:
            return
        from ..settings_store import update_settings
        self.project_id = pid
        self.selected_id = self.undo_action = None
        self._clear_selection()
        self._session_signature = None
        self.session_picker.blockSignals(True)
        self.session_picker.setCurrentIndex(0)
        self.session_picker.blockSignals(False)
        self._clear_notice()
        update_settings({'last_project_id': pid})
        self.refresh()

    def _commit_project(self):
        if not self.project_picker.isEditable() or self.project_picker.currentData() != '__new__':
            return
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
        self.project_picker.setEditable(False)
        self.deck.setFocus()

    def _rename_project(self):
        pid = self.project_picker.currentData()
        if not pid or pid == '__new__':
            return
        with self._child_window():
            name, accepted = get_text(self, 'Rename project', 'Project name', self.project_picker.currentText())
        if not accepted or not name.strip():
            return
        with write_session() as session:
            project = session.get(Project, pid)
            if project:
                project.name = name.strip()[:200]
        self._load_projects()

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
        self._clear_selection()
        self.refresh()

    def _images(self):
        from ..services.image_cards import refresh, output_folder
        if not self.project_id:
            return
        try:
            refresh()
            status = self.status_picker.currentData()
            folder = output_folder(self.project_id, status if status in ('done', 'deleted') else 'open')
            self.hide()
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                self.show()
                self._notify(f'Images folder: {folder}')
        except (OSError, ValueError) as error:
            self._notify(str(error))

    def toggle(self, project_id, work=None):
        if self.isVisible():
            self.hide()
            return
        if self.project_id != project_id:
            self.selected_id = self.undo_action = None
            self._clear_selection()
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
        cid = evidence['capture_id']
        if cid not in self._capture_paths:
            with read_session() as session:
                capture = session.get(Capture, cid)
                self._capture_paths[cid] = media_path(capture.image_rel_path) if capture else None
        path = self._capture_paths[cid]
        if not path:
            return QPixmap()
        try:
            stat = path.stat()
        except OSError:
            return QPixmap()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key in self._back_previews:
            self._back_previews.move_to_end(key)
            return self._back_previews[key]
        if key not in self._preview_pending:
            self._preview_pending.add(key)
            self._preview_workers.submit(self._load_preview, key)
        return QPixmap()

    def _load_preview(self, key):
        reader = QImageReader(key[0])
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(QSize(640, 640), Qt.AspectRatioMode.KeepAspectRatio))
        self.preview_ready.emit(key, reader.read())

    def _preview_loaded(self, key, image):
        self._preview_pending.discard(key)
        self._back_previews[key] = QPixmap.fromImage(image)
        while len(self._back_previews) > 64:
            self._back_previews.popitem(last=False)
        if self.deck.press is None:
            self._present()

    def _detail(self, summary):
        key = (summary['id'], summary['version'])
        if key not in self._details:
            self._details[key] = get_work_item(summary['id'])
        return self._details[key]

    def refresh(self):
        if not self.isVisible() and self.timer.isActive():
            self.timer.stop()
            return
        # Polling must not interrupt a swipe or replace the card under the pointer.
        if self.deck.press is not None:
            return
        self._capture_paths.clear()
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
        self._refresh_record_button(recording)
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
                                 'deleted': 'Deleted tasks — kept here so you can recover them'}.get(status, 'Shift selects a range · Ctrl toggles a card · Drag up to finish, down to delete'))
        ids = [item['id'] for item in self.items]
        had_selection = bool(self.selection)
        self.selection.intersection_update(ids)
        if not self.selection and (had_selection or self.selected_id not in ids):
            self._clear_selection()
        if self.selected_id not in ids:
            remembered = self._saved_selection() if self.selected_id is None else None
            if remembered in ids:
                self.selected_id = remembered
            elif ids:
                self.selected_id = ids[min(previous_index, len(ids) - 1)] if previous_ids and self.selected_id else ids[-1]
            else:
                self.selected_id = None
            self.frame_index = 0
        if not self.selection_explicit:
            self.selection = {self.selected_id} if self.selected_id else set()
            self.selection_anchor = self.selected_id
        index = ids.index(self.selected_id) if ids else 0
        self.position.setText(f'{len(ids)} cards' if ids else '')
        valid = {(item['id'], item['version']) for item in self.items}
        self._details = {key: value for key, value in self._details.items() if key in valid}
        self._present()

    def _present(self):
        ids = [item['id'] for item in self.items]
        index = ids.index(self.selected_id) if self.selected_id in ids else 0
        item = self._detail(self.items[index]) if ids else None
        behind = []
        for relative in (-4, -3, -2, -1, 1, 2, 3, 4):
            neighbour = index + relative
            if 0 <= neighbour < len(self.items):
                full = self._detail(self.items[neighbour])
                behind.append((relative, full, self._pixmap(full)))
        self.deck.present(item, self._pixmap(item) if item else QPixmap(), len(ids), self._direction, behind,
                          ordinal=index + 1 if ids else 0, selected_ids=self.selection,
                          selection_explicit=self.selection_explicit)
        self._direction = 0
        self.deck.preview_loading = bool(self._preview_pending)
        if self.deck.preview_loading and self.deck.isVisible():
            self.deck.spinner.start()
        elif not self.deck.processing_message:
            self.deck.spinner.stop()
        self.update_glass_mask()
        # Warm a wider window of small previews for sustained wheel/drag browsing.
        for neighbour in range(max(0, index - 12), min(len(self.items), index + 13)):
            self._pixmap(self._detail(self.items[neighbour]))

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
        paint_glass(painter, self.rect().adjusted(10, 1, -10, -1), colors.current(), radius=36, strength=.6)
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
        if not self.selection_explicit:
            self.selection = {self.selected_id}
            self.selection_anchor = self.selected_id
        self.frame_index = 0
        self._present()
        self._remember_selection()

    def _jump(self, newest):
        if not self.items:
            return
        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
            index = next((i for i, item in enumerate(self.items) if item['id'] == self.selected_id), 0)
            self._select_cards((len(self.items) - 1 if newest else 0) - index, modifiers)
            return
        self.selected_id = self.items[-1 if newest else 0]['id']
        if not self.selection_explicit:
            self.selection = {self.selected_id}
            self.selection_anchor = self.selected_id
        self._direction = 0
        self._present()
        self._remember_selection()

    def _status(self, value):
        selected = set(self.selection)
        before = [item for item in self.items if item['id'] in selected and item['work_status'] != value]
        if not before:
            return
        try:
            updated = update_work_items([(item['id'], {'work_status': value}, item['version']) for item in before])
            self.undo_action = {
                'changes': [(item['id'], {'work_status': old['work_status']}, item['version'])
                            for old, item in zip(before, updated)],
                'focus': self.selected_id if self.selected_id in selected else before[0]['id'],
                'selection': set(selected),
                'explicit': self.selection_explicit,
            }
            destination = {'done': 'Done', 'deleted': 'Deleted', 'wont_do': 'Archived', 'open': 'Open'}[value]
            message = f'Moved to {destination}.' if len(before) == 1 else f'{len(before)} cards moved to {destination}.'
            self._notify(message, undo=True)
            self.refresh()
        except ValueError as error:
            self._notify(str(error))

    def _undo(self):
        if not self.undo_action:
            return
        action = self.undo_action
        try:
            update_work_items(action['changes'])
            self.undo_action = None
            self.selected_id = action['focus']
            self.selection = set(action['selection'])
            self.selection_anchor = self.selected_id
            self.selection_explicit = action['explicit']
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
        with self._child_window():
            menu.exec(self.deck.mapToGlobal(point))

    def hideEvent(self, event):
        self._remember_selection()
        self.timer.stop()
        self.notice_timer.stop()
        self.deactivate_timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Z:
            self._undo()
        elif event.key() == Qt.Key.Key_Delete:
            self._status('deleted')
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Left):
            direction = 1 if event.key() == Qt.Key.Key_Right else -1
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._select_cards(direction, event.modifiers())
            else:
                self._step(direction)
        else:
            super().keyPressEvent(event)
