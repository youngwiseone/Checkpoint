"""One glass workspace for capture, task notes, recording, and image annotation."""
import re

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                              QSplitter, QStackedWidget, QTabBar, QTextEdit,
                              QVBoxLayout, QWidget)

from ..capture import win32
from ..db import read_session
from ..events import hooks
from ..models import Capture
from ..services.review import get_work_item, update_work_item
from ..storage import media_path
from . import colors
from .glass import GlassDialog, confirm
from .icons import app_logo, control_icon
from .playback import VoicePlayback
from .screenshot import ScreenshotDialog, ScreenshotWorkspace
from .windows import microphone_icon, popup_css


def capture_path(capture_id, original=False):
    from ..services.cropping import original_path
    with read_session() as session:
        capture = session.get(Capture, capture_id)
        if capture is None:
            raise ValueError('Screenshot not found')
        path = original_path(capture) if original else media_path(capture.image_rel_path)
        return path if path.exists() else media_path(capture.image_rel_path)


class CaptureEditor(GlassDialog):
    def __init__(self, item_id=None, parent=None, *, on_save=None, on_keep=None,
                 on_discard=None, on_menu=lambda: None):
        super().__init__(parent)
        if parent is None:
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                                Qt.WindowType.WindowStaysOnTopHint)
        self.on_save, self.on_keep, self.on_discard = on_save, on_keep, on_discard
        self.on_menu = on_menu
        self.item_id, self.item, self.payload = None, None, {}
        self.workspaces = {}
        self._dictation = self._recording = None
        self._recording_capture = None
        self._dictation_save = self._closing = self._snip_pending = False
        self._save_after_snip = False
        self.setWindowTitle('Edit capture')
        self.resize(1460, 880)
        self.setMinimumSize(900, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(app_logo(self))
        self.heading = QLabel('Capture')
        self.heading.setStyleSheet('font-size: 17px; font-weight: 600;')
        header.addWidget(self.heading)
        self.image_tabs = QTabBar()
        self.image_tabs.setExpanding(False)
        self.image_tabs.setUsesScrollButtons(True)
        self.image_tabs.setTabsClosable(True)
        self.image_tabs.setMinimumWidth(0)
        self.image_tabs.setAccessibleName('Task screenshots')
        self.image_tabs.currentChanged.connect(self._select_image)
        self.image_tabs.tabCloseRequested.connect(self._remove_image)
        header.addWidget(self.image_tabs, 1)
        self.add_image_btn = QPushButton('+')
        self.add_image_btn.setObjectName('glassCircle')
        self.add_image_btn.setAccessibleName('Take another screenshot')
        self.add_image_btn.setToolTip('Take another screenshot and add it to this task')
        self.add_image_btn.clicked.connect(self._snip)
        header.addWidget(self.add_image_btn)
        self.close_button = QPushButton()
        self.close_button.setObjectName('glassCircle')
        self.close_button.setIcon(control_icon('close'))
        self.close_button.setAccessibleName('Save and close capture')
        self.close_button.setToolTip('Save and close')
        self.close_button.clicked.connect(self._keep)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.images = QStackedWidget()
        self.empty_image = QLabel('No image attached.\nUse + to take a screenshot.')
        self.empty_image.setObjectName('imagePreview')
        self.empty_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.images.addWidget(self.empty_image)
        self.splitter.addWidget(self.images)
        sidebar = QWidget()
        sidebar.setMinimumWidth(280)
        sidebar.setMaximumWidth(440)
        notes = QVBoxLayout(sidebar)
        notes.setContentsMargins(8, 4, 0, 0)
        notes.setSpacing(10)
        title = QLabel('Note & transcript')
        title.setStyleSheet('font-size: 17px; font-weight: 600;')
        notes.addWidget(title)
        self.text = self.description = QPlainTextEdit()
        self.text.setAccessibleName('Note')
        self.text.setPlaceholderText('Describe the change, or dictate a note…')
        self.text.setToolTip('Select recorded words and press Play to hear that part.')
        self.text.setMinimumHeight(130)
        notes.addWidget(self.text, 1)
        self.dictation_status = QLabel('Ready to annotate or add a note.')
        self.dictation_status.setObjectName('muted')
        self.dictation_status.setWordWrap(True)
        notes.addWidget(self.dictation_status)
        voice = QHBoxLayout()
        self.play_btn = self.play_button = QPushButton()
        self.play_btn.setObjectName('glassCircle')
        self.play_btn.setIcon(control_icon('play'))
        self.play_btn.setAccessibleName('Play voice note')
        self.playback = self.voice_playback = VoicePlayback(self.play_btn, self._show_error)
        self.play_btn.hide()
        voice.addWidget(self.play_btn)
        self.mic_btn = QPushButton('Dictate note')
        self.mic_btn.setIcon(microphone_icon())
        self.mic_btn.clicked.connect(self._toggle_dictation)
        voice.addWidget(self.mic_btn, 1)
        notes.addLayout(voice)
        self.words_button = QPushButton('Captured words')
        self.words_button.setCheckable(True)
        self.words = QPlainTextEdit()
        self.words.setAccessibleName('Captured words')
        self.words.setReadOnly(True)
        self.words.setMaximumHeight(150)
        self.words.hide()
        self.words_button.toggled.connect(self.words.setVisible)
        notes.addWidget(self.words_button)
        notes.addWidget(self.words)
        self.bundle_btn = QPushButton('Add to previous task')
        self.bundle_btn.clicked.connect(self._bundle)
        notes.addWidget(self.bundle_btn)
        self.menu_btn = QPushButton('Open menu')
        self.menu_btn.clicked.connect(self._open_menu)
        notes.addWidget(self.menu_btn)
        self.splitter.addWidget(sidebar)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([1100, 320])
        layout.addWidget(self.splitter, 1)
        self.error = self.message = QLabel()
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        footer = QHBoxLayout()
        self.discard_btn = QPushButton('Discard task')
        self.discard_btn.setObjectName('danger')
        self.discard_btn.clicked.connect(self._discard)
        footer.addWidget(self.discard_btn)
        self.sub = QLabel('Your note and all images save together. Ctrl+S to save.')
        self.sub.setObjectName('muted')
        self.sub.setWordWrap(True)
        footer.addWidget(self.sub, 1)
        self.save_btn = QPushButton('Save')
        self.save_btn.setObjectName('primary')
        self.save_btn.clicked.connect(self._save)
        footer.addWidget(self.save_btn)
        layout.addLayout(footer)
        for key in ('Ctrl+S', 'Ctrl+Return', 'Ctrl+Enter'):
            QShortcut(QKeySequence(key), self, activated=self._save)
        self.text.selectionChanged.connect(self._select_voice)
        self.dictation_timer = QTimer(self, interval=100, timeout=self._poll_dictation)
        self.context_timer = QTimer(self, interval=650, timeout=self._poll_context)
        if item_id:
            self._load_payload({'item_id': item_id})

    def _apply_theme(self):
        self._theme = colors.current().name
        self.setStyleSheet(popup_css(colors.current()))

    def _load_payload(self, payload):
        payload = dict(payload)
        if payload.get('id'):
            from ..services.task_evidence import capture_editor_payload
            try:
                payload = {**payload, **capture_editor_payload(payload['id'])}
            except ValueError:
                pass
        self.payload = payload
        self.item_id = payload.get('item_id')
        self.item = get_work_item(self.item_id) if self.item_id else None
        if self.item:
            self.payload.update(project_id=self.item['project_id'], text=self.item['description'],
                                evidence=self.item['evidence'])
        self.text.setPlainText(self.payload.get('text', ''))
        self.error.hide()
        self._refresh_images()
        self._refresh_words()
        self.add_image_btn.setEnabled(bool(self.item_id))
        self.bundle_btn.setVisible(bool(self.item_id))
        self.discard_btn.setVisible(bool(self.item_id or self.payload.get('id')))
        self.heading.setText('Capture' if self.workspaces else 'Task note')
        self.playback.set_evidence(self.payload.get('evidence', []))
        self._highlight_uncertain()

    def open_for(self, payload):
        same = bool(self.payload) and (payload.get('item_id') == self.item_id if payload.get('item_id')
                                      else payload.get('id') == self.payload.get('id'))
        if same and self.isVisible():
            self.raise_()
            self.activateWindow()
            return
        if self.isVisible() and (self._recording or self._dictation):
            self._show_error('Finish this recording before opening another task.')
            return
        if self.isVisible() and not self._commit():
            return
        self._cancel_dictation()
        self.playback.stop()
        self._clear_images()
        self._apply_theme()
        self._load_payload(payload)
        self.show()
        self.raise_()
        self.activateWindow()
        win32.restore_foreground(int(self.winId()))
        self._focus_capture_input()
        QTimer.singleShot(0, self._focus_capture_input)

    def _focus_capture_input(self):
        if not self.isVisible():
            return
        dictated = self._recording or self._dictation or any(
            entry.get('note_kind') == 'voice' or entry.get('kind') == 'segment'
            for entry in self.payload.get('evidence', []))
        if dictated and self.workspaces:
            self.images.currentWidget().image.setFocus(Qt.FocusReason.ShortcutFocusReason)
        else:
            self.text.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def showEvent(self, event):
        super().showEvent(event)
        win32.exclude_from_capture(int(self.winId()))
        self.context_timer.start()

    def _clear_images(self):
        self.image_tabs.blockSignals(True)
        while self.image_tabs.count():
            self.image_tabs.removeTab(0)
        for workspace in self.workspaces.values():
            self.images.removeWidget(workspace)
            workspace.deleteLater()
        self.workspaces.clear()
        self.image_tabs.blockSignals(False)

    def _refresh_images(self, select_new=False):
        selected = self.image_tabs.tabData(self.image_tabs.currentIndex())
        captures = [e for e in self.payload.get('evidence', []) if e['kind'] == 'capture']
        ids = {e['capture_id'] for e in captures}
        for cid in set(self.workspaces) - ids:
            workspace = self.workspaces.pop(cid)
            self.images.removeWidget(workspace)
            workspace.deleteLater()
        self.image_tabs.blockSignals(True)
        while self.image_tabs.count():
            self.image_tabs.removeTab(0)
        for index, entry in enumerate(captures):
            cid = entry['capture_id']
            if entry.get('media_removed'):
                continue
            try:
                if cid not in self.workspaces:
                    self.workspaces[cid] = ScreenshotWorkspace(cid, self)
                    self.images.addWidget(self.workspaces[cid])
                workspace = self.workspaces[cid]
                tab = self.image_tabs.addTab(QIcon(workspace.image.source.scaled(64, 40,
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)), f'Image {index + 1}')
                self.image_tabs.setTabData(tab, cid)
                close = QPushButton()
                close.setIcon(control_icon('close'))
                close.setIconSize(QSize(12, 12))
                close.setFixedSize(22, 22)
                close.setAccessibleName(f'Delete image {index + 1}')
                close.setToolTip('Delete this image')
                close.setStyleSheet('QPushButton { background: transparent; border: none; border-radius: 11px; '
                                    'padding: 2px; min-height: 18px; min-width: 18px; } '
                                    'QPushButton:hover { background: rgba(120, 140, 165, 40); }')
                close.clicked.connect(lambda checked=False, capture_id=cid: self._remove_image(next(
                    (i for i in range(self.image_tabs.count()) if self.image_tabs.tabData(i) == capture_id), -1)))
                self.image_tabs.setTabButton(tab, QTabBar.ButtonPosition.RightSide, close)
            except (ValueError, OSError) as error:
                self._show_error(str(error))
        selected_index = next((i for i in range(self.image_tabs.count())
                               if self.image_tabs.tabData(i) == selected), 0)
        if select_new:
            selected_index = self.image_tabs.count() - 1
        self.image_tabs.setCurrentIndex(selected_index)
        self.image_tabs.blockSignals(False)
        self._select_image(self.image_tabs.currentIndex())

    def _select_image(self, index):
        cid = self.image_tabs.tabData(index)
        if cid in self.workspaces:
            self.payload['id'] = cid
            self.images.setCurrentWidget(self.workspaces[cid])
        else:
            self.payload.pop('id', None)
            self.images.setCurrentWidget(self.empty_image)

    def _reload_evidence(self, select_new=False, accept_changes=False):
        if not self.item_id:
            return
        current = get_work_item(self.item_id)
        if accept_changes or not current['description_edited'] or current['description'] == self.payload.get('text', ''):
            self.item = current
        else:
            self._show_error('This task changed elsewhere. Your draft is kept; reopen it before saving.')
        self.payload['evidence'] = current['evidence']
        self._refresh_images(select_new)
        self._refresh_words()
        self.playback.set_evidence(current['evidence'])
        self._highlight_uncertain()

    def _refresh_words(self):
        quotes = [f"{e.get('source_label') or ('Voice note' if e.get('note_kind') == 'voice' else 'Captured note')}:\n{e['text']}"
                  for e in self.payload.get('evidence', []) if e['kind'] != 'capture' and e.get('text')]
        text = '\n\n'.join(quotes)
        if self.words.toPlainText() != text:
            self.words.setPlainText(text)
        self.words_button.setVisible(bool(quotes))
        if not quotes:
            self.words_button.setChecked(False)

    def _poll_context(self):
        if self._recording:
            self._recording_status(self._recording)
        if not self.item_id or not self.isVisible():
            return
        try:
            current = get_work_item(self.item_id)
            if not current['description_edited']:
                incoming = current['description']
                baseline = self.payload.get('text', '')
                if incoming != baseline:
                    if self.text.toPlainText() == baseline:
                        self.text.setPlainText(incoming)
                    elif incoming and incoming not in self.text.toPlainText():
                        self.text.appendPlainText(incoming)
                    self.payload['text'] = incoming
                self.item = current
            self.payload['evidence'] = current['evidence']
            self._refresh_words()
            self._highlight_uncertain()
            if not (self._dictation or self._recording) and self.item.get('session_id'):
                from ..transcription.worker import transcription_status
                counts = transcription_status(self.item['session_id'])
                if counts.get('queued') or counts.get('running'):
                    self.dictation_status.setText('Processing recorded speech… You can keep annotating.')
                elif self.dictation_status.text().startswith('Processing recorded speech'):
                    self.dictation_status.setText('Transcript ready. You can edit the note and save.')
        except (ValueError, OSError):
            pass

    def _commit(self, force=False):
        try:
            if self.item_id:
                current = get_work_item(self.item_id)
                if self.item and current['version'] != self.item['version']:
                    raise ValueError('This task changed elsewhere. Reopen it before saving your changes.')
            for workspace in self.workspaces.values():
                workspace.commit()
            if self.item_id:
                self.item = get_work_item(self.item_id)
                self.payload['version'] = self.item['version']
            text = self.text.toPlainText()
            changed = text != self.payload.get('text', '')
            if self.on_save and (changed or force) and (text.strip() or self.item_id):
                error = self.on_save(dict(self.payload), text, None)
                if error:
                    raise ValueError(error)
            elif self.item_id and changed:
                update_work_item(self.item_id, {'description': text}, self.item['version'])
            elif not self.item_id and not text.strip() and not self.payload.get('id'):
                raise ValueError('Write a note or add a screenshot before saving.')
            if self.item_id:
                self.item = get_work_item(self.item_id)
                hooks.emit_task_ready(self.item_id)
            self.payload['text'] = text
            self.error.hide()
            return True
        except (ValueError, OSError) as error:
            self._show_error(str(error))
            return False

    def _persist_editor(self):
        return self._commit()

    def _save(self):
        recording = self._dictation or self._recording
        if recording:
            self._dictation_save = True
            recording.stop()
            self.mic_btn.setEnabled(False)
            self.dictation_status.setText('Transcribing before saving…')
            return
        if self._snip_pending:
            self._save_after_snip = True
            self.dictation_status.setText('Finishing screenshot before saving…')
            return
        if self._commit(force=True):
            self._close(self.DialogCode.Accepted)

    def _keep(self):
        if not self.payload:
            self._close()
        else:
            self._save()

    def _discard(self):
        if not confirm(self, 'Discard task?', 'Delete this task and its attached images? You can restore it from the Deleted view.'):
            return
        try:
            if self.on_discard:
                error = self.on_discard(self.payload)
                if error:
                    raise ValueError(error)
            elif self.item_id:
                update_work_item(self.item_id, {'work_status': 'deleted'})
            if self._recording:
                self._recording.cancel()
                self._recording = None
            self._close()
        except ValueError as error:
            self._show_error(str(error))

    def _remove_image(self, index=None):
        if index is None:
            index = self.image_tabs.currentIndex()
        cid = self.image_tabs.tabData(index)
        if not cid:
            return
        if ((self._recording and cid == self._recording_capture) or
                (self._dictation and cid == getattr(self, '_dictation_capture', None))):
            self._show_error('Finish the recording before deleting its screenshot.')
            return
        if not confirm(self, 'Delete image?', 'Delete this image from the task? The note and other images will be kept.'):
            return
        try:
            from ..services.task_evidence import detach_capture
            detach_capture(self.item_id, cid)
            self._reload_evidence()
        except ValueError as error:
            self._show_error(str(error))

    def _snip(self):
        if not self.item_id or self._snip_pending:
            return
        from .snipping import snip_into
        self._snip_pending = True
        def done(error):
            self._snip_pending = False
            save = self._save_after_snip
            self._save_after_snip = False
            if error:
                self._show_error(error)
            else:
                self._reload_evidence(select_new=True)
                if save:
                    # snip_into re-shows its parent after this callback returns.
                    QTimer.singleShot(0, self._save)
        snip_into(self.item_id, self, done)

    def _bundle(self):
        if self._recording or self._dictation:
            self._show_error('Finish recording before combining tasks.')
            return
        if self._commit():
            from ..services.task_evidence import bundle_previous
            try:
                self.item_id = bundle_previous(self.item_id)
                self.payload['item_id'] = self.item_id
                self.item = get_work_item(self.item_id)
                self.text.setPlainText(self.item['description'])
                self.payload['text'] = self.item['description']
                self._reload_evidence()
            except ValueError as error:
                self._show_error(str(error))

    def _open_menu(self):
        if self._recording or self._dictation:
            self._show_error('Finish recording before opening the menu.')
        elif self._commit():
            self._close(self.DialogCode.Accepted)
            self.on_menu()

    def set_recording(self, recording):
        self._recording = recording
        self._recording_capture = self.payload.get('id')
        self.mic_btn.setText('Stop recording')
        self._recording_status(recording)
        self._focus_capture_input()

    def _recording_status(self, recording):
        if self._dictation_save:
            self.dictation_status.setText('Transcribing before saving…')
        else:
            self.dictation_status.setText({'starting': 'Opening microphone…',
                'recording': 'Listening… Release F9 or click Stop recording when finished.',
                'transcribing': 'Transcribing… You can keep drawing and typing.'}.get(recording.phase, 'Finishing transcript…'))

    def complete_recording(self, text='', error=''):
        self._recording = None
        save = self._dictation_save
        self._dictation_save = False
        self.mic_btn.setEnabled(True)
        self.mic_btn.setText('Dictate note')
        if text and text not in self.text.toPlainText():
            self.text.setPlainText('\n\n'.join(part for part in (self.text.toPlainText(), text) if part))
        self._reload_evidence(accept_changes=True)
        if self.item:
            self.payload['text'] = self.item['description']
        self.dictation_status.setText('Transcript added. Edit it or save.' if text else 'No speech detected. You can type a note or save the image.')
        if error:
            self._show_error(error)
        if save:
            self._save()

    def _toggle_dictation(self):
        if self._recording:
            self._recording.stop()
            self.mic_btn.setEnabled(False)
            self.dictation_status.setText('Transcribing… You can keep drawing and typing.')
            return
        if self._dictation:
            self._dictation.stop()
            self.mic_btn.setEnabled(False)
            self.dictation_status.setText('Transcribing…')
            return
        from ..audio.dictation import NoteDictation
        from ..settings_store import get_settings
        from ..transcription.engine import model_installed
        if not model_installed('whistle'):
            self._show_error('Download Whistle in Settings → Transcript to dictate notes.')
            return
        self.error.hide()
        self._dictation_save = False
        self._dictation = NoteDictation(get_settings().audio.mic_device)
        self._dictation.project_id = self.payload.get('project_id')
        self._dictation_capture = self.payload.get('id')
        self.mic_btn.setText('Stop dictation')
        self.dictation_status.setText('Opening microphone…')
        self._dictation.start()
        self.dictation_timer.start()

    def _poll_dictation(self):
        recording = self._dictation
        if recording is None:
            return
        cid = getattr(self, '_dictation_capture', None)
        audio = getattr(recording, 'audio', b'')
        if audio and cid and not getattr(recording, 'retained_note_id', None):
            from ..services.voice import retain
            try:
                recording.retained_note_id = retain(cid, audio)
                self._reload_evidence()
            except (ValueError, OSError) as error:
                self._show_error('Could not retain voice recording: ' + str(error))
                return
        if recording.phase != 'done':
            self._recording_status(recording)
            return
        self._dictation = None
        self.dictation_timer.stop()
        save = self._dictation_save
        self._dictation_save = False
        self.mic_btn.setEnabled(True)
        self.mic_btn.setText('Dictate note')
        if getattr(recording, 'retained_note_id', None):
            from ..services.voice import finish_note
            finish_note(recording.retained_note_id, recording.text, getattr(recording, 'words', []))
            self._reload_evidence()
        if recording.error:
            self._show_error("Couldn't dictate: " + recording.error)
            return
        if not recording.text:
            self.dictation_status.setText('No speech detected. Try again or type your note.')
            return
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText((' ' if self.text.toPlainText() and not self.text.toPlainText()[-1].isspace() else '') + recording.text)
        self.text.setTextCursor(cursor)
        self.dictation_status.setText('Dictation added. Edit it or save your note.')
        self._highlight_uncertain()
        if save:
            self._save()

    def _cancel_dictation(self):
        if self._dictation:
            self._dictation.cancel()
        self._dictation = None
        self._dictation_save = False
        self.dictation_timer.stop()
        self.mic_btn.setEnabled(True)
        self.mic_btn.setText('Dictate note')

    def _highlight_uncertain(self):
        selections = []
        text = self.text.toPlainText()
        for entry in self.payload.get('evidence', []):
            for word in entry.get('speech_words') or []:
                if not word.get('low'):
                    continue
                for match in re.finditer(re.escape(word['text']), text, re.I):
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = self.text.textCursor()
                    selection.cursor.setPosition(len(text[:match.start()].encode('utf-16-le')) // 2)
                    selection.cursor.setPosition(len(text[:match.end()].encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor)
                    selection.format.setUnderlineStyle(selection.format.UnderlineStyle.DotLine)
                    selection.format.setUnderlineColor(colors.current().warn)
                    selections.append(selection)
        self.text.setExtraSelections(selections)

    def _select_voice(self):
        selected = self.text.textCursor().selectedText().strip().lower()
        evidence = self.payload.get('evidence', [])
        self.playback.set_evidence(evidence)
        if not selected:
            return
        for entry in evidence:
            words = entry.get('speech_words') or []
            for index, word in enumerate(words):
                for end in range(index, min(len(words), index + 12)):
                    if ' '.join(w['text'] for w in words[index:end + 1]).strip().lower() == selected and entry.get('audio_url'):
                        self.playback.entries = [{**entry, 'start_s': word['start'], 'end_s': words[end]['end']}]
                        self.play_btn.setToolTip('Play selected words')
                        return

    def _show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    _show_message = _show_error

    def _fit(self):
        self.layout().activate()

    def _close(self, result=0):
        self._cancel_dictation()
        self.context_timer.stop()
        self.playback.stop()
        previous = self.payload.get('fg_hwnd') or 0
        self._closing = True
        self.done(result)
        self._closing = False
        if previous:
            win32.restore_foreground(previous)
        self.payload = {}

    def reject(self):
        self._keep()

    def closeEvent(self, event):
        if self._closing:
            event.accept()
        else:
            event.ignore()
            self._keep()


class TaskDialog(CaptureEditor):
    """The menu uses exactly the same workspace as automatic and F9 editing."""

    def __init__(self, item_id, parent=None):
        super().__init__(item_id, parent)
