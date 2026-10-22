"""Native task and screenshot editors."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialogButtonBox, QHBoxLayout, QLabel,
                              QPlainTextEdit, QPushButton, QSizePolicy)

from ..db import read_session
from ..models import Capture
from ..services.review import get_work_item, update_work_item
from ..storage import media_path
from .windows import popup_css
from . import colors
from .glass import GlassDialog
from .icons import control_icon
from .screenshot import ScreenshotDialog


def capture_path(capture_id, original=False):
    from ..services.cropping import original_path
    with read_session() as s:
        capture = s.get(Capture, capture_id)
        if capture is None:
            raise ValueError("Screenshot not found")
        path = original_path(capture) if original else media_path(capture.image_rel_path)
        return path if path.exists() else media_path(capture.image_rel_path)


class TaskImagePreview(QLabel):
    """A bounded preview with image actions and navigation over the image."""

    def __init__(self):
        super().__init__()
        self.source = QPixmap()
        self.setObjectName('imagePreview')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumHeight(112)
        self.setMaximumHeight(210)
        self.previous_button = self._button('left', 'Previous screenshot')
        self.next_button = self._button('right', 'Next screenshot')
        self.edit_button = self._button('pencil', 'Edit screenshot')
        self.remove_button = self._button('trash', 'Remove screenshot')
        self.counter = QLabel(self)
        self.counter.setObjectName('counter')
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _button(self, icon, label):
        button = QPushButton(self)
        button.setObjectName('glassCircle')
        button.setFixedSize(44, 44)
        button.setIcon(control_icon(icon))
        button.setAccessibleName(label)
        button.setToolTip(label)
        return button

    def load(self, path):
        self.source = QPixmap(str(path))
        self._layout_preview()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_preview()

    def _layout_preview(self):
        if not self.source.isNull():
            self.setPixmap(self.source.scaled(self.width() - 8, self.height() - 8,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.edit_button.move(self.width() - 104, 8)
        self.remove_button.move(self.width() - 52, 8)
        self.previous_button.move(8, self.height() - 52)
        self.next_button.move(self.width() - 52, self.height() - 52)
        self.counter.adjustSize()
        self.counter.move((self.width() - self.counter.width()) // 2,
                          self.height() - self.counter.height() - 8)


class TaskDialog(GlassDialog):
    def __init__(self, item_id, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.item = get_work_item(item_id)
        self.setWindowTitle("Edit task")
        self.resize(780, 820)
        self.setStyleSheet(popup_css(colors.current()))
        layout = self.content_layout()
        layout.setSpacing(12)
        self.captures = [e for e in self.item["evidence"] if e["kind"] == "capture"]
        self.image_index = 0
        self.screenshot = TaskImagePreview()
        layout.addWidget(self.screenshot, 1)
        self.previous_image_btn = self.screenshot.previous_button
        self.next_image_btn = self.screenshot.next_button
        self.edit_image_btn = self.screenshot.edit_button
        self.remove_image_btn = self.screenshot.remove_button
        self.previous_image_btn.clicked.connect(lambda: self._navigate_image(-1))
        self.next_image_btn.clicked.connect(lambda: self._navigate_image(1))
        self.edit_image_btn.clicked.connect(self._edit_image)
        self.remove_image_btn.clicked.connect(self._remove_image)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        from .playback import VoicePlayback
        self.play_button = QPushButton()
        self.play_button.setObjectName('glassCircle')
        self.play_button.setFixedSize(44, 44)
        self.play_button.setIcon(control_icon('play'))
        self.play_button.setAccessibleName('Play voice note')
        self.voice_playback = VoicePlayback(self.play_button, self._show_message)
        self.voice_playback.set_evidence(self.item['evidence'])
        self.finished.connect(lambda _: self.voice_playback.stop())
        actions.addWidget(self.play_button)
        for label, callback in (('Add to last task', self._bundle), ('Snip image', self._snip), ('Open menu', self._menu)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button, 1)
        self.words_button = QPushButton('Captured words')
        self.words_button.setCheckable(True)
        actions.addWidget(self.words_button)
        layout.addLayout(actions)
        self.description = QPlainTextEdit(self.item["description"])
        self.description.setAccessibleName("Note")
        self.description.setMinimumHeight(240)
        layout.addWidget(QLabel("Note"))
        layout.addWidget(self.description, 3)
        self.words = QPlainTextEdit()
        self.words.setAccessibleName('Captured words')
        self.words.setReadOnly(True)
        self.words.setMaximumHeight(96)
        self.words.hide()
        self.words_button.toggled.connect(self.words.setVisible)
        layout.addWidget(self.words)
        self._refresh_words()
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('primary')
        layout.addWidget(buttons)
        self._image()

    def _current_capture_id(self):
        return self.captures[self.image_index]['capture_id'] if self.captures else None

    def _show_message(self, text):
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _navigate_image(self, direction):
        self.image_index = max(0, min(len(self.captures) - 1, self.image_index + direction))
        self._image()

    def _image(self):
        has_image = bool(self.captures)
        self.screenshot.setVisible(has_image)
        self.edit_image_btn.setEnabled(has_image)
        self.remove_image_btn.setEnabled(has_image)
        self.previous_image_btn.setEnabled(has_image and self.image_index > 0)
        self.next_image_btn.setEnabled(has_image and self.image_index < len(self.captures) - 1)
        self.previous_image_btn.setVisible(len(self.captures) > 1)
        self.next_image_btn.setVisible(len(self.captures) > 1)
        if not has_image:
            return
        capture = self.captures[self.image_index]
        self.screenshot.counter.setText(f'{self.image_index + 1} / {len(self.captures)}' +
            (' · possibly related' if capture['confidence'] != 'direct' else ''))
        self.screenshot.load(capture_path(capture['capture_id']))

    def _edit_image(self):
        if not self._commit():
            return
        ScreenshotDialog(self._current_capture_id(), self).exec()
        self._refresh_evidence()

    def _refresh_evidence(self):
        selected = self._current_capture_id()
        current = get_work_item(self.item_id)
        self.item['version'] = current['version']
        self.item['evidence'] = current['evidence']
        self.voice_playback.set_evidence(current['evidence'])
        self.captures = [e for e in current['evidence'] if e['kind'] == 'capture']
        self.image_index = next((index for index, capture in enumerate(self.captures)
            if capture['capture_id'] == selected), min(self.image_index, max(0, len(self.captures) - 1)))
        self._refresh_words()
        self._image()

    def _refresh_words(self):
        quotes = [f"{'Typed note' if e['kind'] == 'note' else e.get('source_label', 'Transcript')}{' (withdrawn)' if e.get('role') == 'withdrawal' else ''}: {e['text']}"
                  for e in self.item['evidence'] if e['kind'] != 'capture']
        self.words.setPlainText('\n\n'.join(quotes))
        self.words_button.setVisible(bool(quotes))
        if not quotes:
            self.words_button.setChecked(False)

    def _remove_image(self):
        if not self._commit():
            return
        from ..services.task_evidence import detach_capture
        try:
            detach_capture(self.item_id, self._current_capture_id())
            self._refresh_evidence()
        except ValueError as error:
            self._show_message(str(error))

    def _commit(self):
        try:
            self.item = update_work_item(self.item_id, {'description': self.description.toPlainText()}, self.item['version'])
            return True
        except ValueError as error:
            self._show_message(str(error))
            return False

    def _bundle(self):
        if not self._commit():
            return
        from ..services.task_evidence import bundle_previous
        try:
            self.item_id = bundle_previous(self.item_id)
            self.item = get_work_item(self.item_id)
            self.description.setPlainText(self.item['description'])
            self._refresh_evidence()
        except ValueError as error:
            self._show_message(str(error))

    def _snip(self):
        if not self._commit():
            return
        from .snipping import snip_into
        def done(error):
            if error:
                self._show_message(error)
            else:
                self._refresh_evidence()
        snip_into(self.item_id, self, done)

    def _menu(self):
        if self._commit():
            self.accept()

    def _save(self):
        if self._commit():
            self.accept()
