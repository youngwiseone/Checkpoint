"""Native task and screenshot editors."""
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialogButtonBox, QHBoxLayout, QLabel,
                              QPlainTextEdit, QPushButton)

from ..db import read_session
from ..models import Capture
from ..services.review import get_work_item, update_work_item
from ..storage import media_path
from .windows import popup_css
from . import colors
from .glass import GlassDialog


def capture_path(capture_id, original=False):
    from ..services.cropping import original_path
    with read_session() as s:
        capture = s.get(Capture, capture_id)
        if capture is None:
            raise ValueError("Screenshot not found")
        path = original_path(capture) if original else media_path(capture.image_rel_path)
        return path if path.exists() else media_path(capture.image_rel_path)


class SelectionImage(QLabel):
    """Draw a selection in image coordinates, excluding letterbox margins."""
    def __init__(self):
        super().__init__()
        self.source = QPixmap()
        self.selection = None
        self.start = None
        self.setMinimumSize(260, 220)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def load(self, path):
        self.source = QPixmap(str(path))
        self.selection = self.start = None
        self.update()

    def image_rect(self):
        if self.source.isNull():
            return QRect()
        size = self.source.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect((self.width() - size.width()) // 2, (self.height() - size.height()) // 2, size.width(), size.height())

    def _point(self, point):
        rect = self.image_rect()
        return QPoint(max(rect.left(), min(rect.right(), point.x())), max(rect.top(), min(rect.bottom(), point.y())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position().toPoint()):
            self.start = self._point(event.position().toPoint())
            self.selection = QRect(self.start, self.start)
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.selection = QRect(self.start, self._point(event.position().toPoint())).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is not None:
            self.selection = QRect(self.start, self._point(event.position().toPoint())).normalized()
            self.start = None
            self.update()

    def box(self):
        rect = self.image_rect()
        if self.selection is None or self.selection.width() < 3 or self.selection.height() < 3 or rect.isEmpty():
            raise ValueError("Drag a rectangle over the image first.")
        x = int((self.selection.left() - rect.left()) * self.source.width() / rect.width())
        y = int((self.selection.top() - rect.top()) * self.source.height() / rect.height())
        right = min(self.source.width(), round((self.selection.right() + 1 - rect.left()) * self.source.width() / rect.width()))
        bottom = min(self.source.height(), round((self.selection.bottom() + 1 - rect.top()) * self.source.height() / rect.height()))
        return x, y, max(1, right - x), max(1, bottom - y)

    def resizeEvent(self, event):
        self.selection = self.start = None
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(colors.current().border, 1))
        painter.setBrush(colors.current().field)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)
        if self.source.isNull():
            painter.setPen(colors.current().muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Screenshot unavailable")
            return
        painter.drawPixmap(self.image_rect(), self.source)
        if self.selection:
            painter.setPen(QPen(QColor("#ff4040"), 2))
            painter.drawRect(self.selection)


class ScreenshotDialog(GlassDialog):
    def __init__(self, capture_id, parent=None):
        super().__init__(parent)
        self.capture_id = capture_id
        self.setWindowTitle("Edit image")
        self.resize(900, 650)
        self.setStyleSheet(popup_css(colors.current()))
        layout = self.content_layout()
        self.mode = QComboBox()
        self.mode.addItem("Crop from original", "crop")
        self.mode.addItem("Highlight rectangle on current image", "highlight")
        layout.addWidget(self.mode)
        self.image = SelectionImage()
        layout.addWidget(self.image, 1)
        self.message = QLabel("Drag to select an area. The original is always preserved.")
        self.message.setObjectName('muted')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        actions = QHBoxLayout()
        for label, callback in (("Restore original", lambda: self._save(True)), ("Cancel", self.reject), ("Save", self._save)):
            button = QPushButton(label)
            if label == 'Save':
                button.setObjectName('primary')
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.mode.currentIndexChanged.connect(self._load)
        self._load()

    def _load(self):
        self.image.load(capture_path(self.capture_id, self.mode.currentData() == "crop"))

    def _save(self, reset=False):
        from ..services.cropping import crop
        try:
            crop(self.capture_id, None if reset else self.image.box(), highlight=not reset and self.mode.currentData() == "highlight")
            self.accept()
        except (ValueError, OSError) as error:
            self.message.setText(str(error))


class TaskDialog(GlassDialog):
    def __init__(self, item_id, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.item = get_work_item(item_id)
        self.setWindowTitle("Edit task")
        self.resize(780, 720)
        self.setStyleSheet(popup_css(colors.current()))
        layout = self.content_layout()
        self.captures = [e for e in self.item["evidence"] if e["kind"] == "capture"]
        self.screenshot = QLabel()
        self.screenshot.setObjectName('imagePreview')
        self.screenshot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.screenshot)
        actions = QHBoxLayout()
        self.picker = QComboBox()
        for index, capture in enumerate(self.captures):
            self.picker.addItem(f"Screenshot {index + 1}" + (" · possibly related" if capture["confidence"] != "direct" else ""), capture["capture_id"])
        self.picker.currentIndexChanged.connect(self._image)
        actions.addWidget(self.picker, 1)
        from .icons import control_icon
        from .playback import VoicePlayback
        self.play_button = QPushButton()
        self.play_button.setObjectName('glassCircle')
        self.play_button.setFixedSize(44, 44)
        self.play_button.setIcon(control_icon('play'))
        self.play_button.setAccessibleName('Play voice note')
        self.voice_playback = VoicePlayback(self.play_button, lambda error: self.message.setText(error))
        self.voice_playback.set_evidence(self.item['evidence'])
        self.finished.connect(lambda _: self.voice_playback.stop())
        actions.addWidget(self.play_button)
        edit = QPushButton("Edit screenshot")
        edit.clicked.connect(self._edit_image)
        actions.addWidget(edit)
        self.edit_image_btn = edit
        remove = QPushButton('Remove image')
        remove.clicked.connect(self._remove_image)
        actions.addWidget(remove)
        self.remove_image_btn = remove
        layout.addLayout(actions)
        self._image()
        extras = QHBoxLayout()
        for label, callback in (('Add to last task', self._bundle), ('Snip image', self._snip), ('Open menu', self._menu)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            extras.addWidget(button)
        layout.addLayout(extras)
        self.description = QPlainTextEdit(self.item["description"])
        self.description.setAccessibleName("Note")
        layout.addWidget(QLabel("Note"))
        layout.addWidget(self.description, 1)
        quotes = [f"{'Typed note' if e['kind'] == 'note' else e.get('source_label', 'Transcript')}{' (withdrawn)' if e.get('role') == 'withdrawal' else ''}: {e['text']}"
                  for e in self.item["evidence"] if e["kind"] != "capture"]
        if quotes:
            evidence = QPlainTextEdit("\n\n".join(quotes))
            evidence.setReadOnly(True)
            evidence.setMaximumHeight(130)
            layout.addWidget(QLabel("Captured words"))
            layout.addWidget(evidence)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('primary')
        layout.addWidget(buttons)

    def _image(self):
        has_image = bool(self.picker.currentData())
        self.screenshot.setVisible(has_image)
        self.edit_image_btn.setEnabled(has_image)
        self.remove_image_btn.setEnabled(has_image)
        if not has_image:
            return
        pixmap = QPixmap(str(capture_path(self.picker.currentData())))
        self.screenshot.setPixmap(pixmap.scaled(700, 280, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _edit_image(self):
        if not self._commit():
            return
        ScreenshotDialog(self.picker.currentData(), self).exec()
        self._refresh_evidence()

    def _refresh_evidence(self):
        current = get_work_item(self.item_id)
        self.item['version'] = current['version']
        self.item['evidence'] = current['evidence']
        self.voice_playback.set_evidence(current['evidence'])
        self.captures = [e for e in current['evidence'] if e['kind'] == 'capture']
        self.picker.blockSignals(True)
        self.picker.clear()
        for index, capture in enumerate(self.captures, 1):
            self.picker.addItem(f'Image {index}', capture['capture_id'])
        self.picker.blockSignals(False)
        self._image()

    def _remove_image(self):
        if not self._commit():
            return
        from ..services.task_evidence import detach_capture
        try:
            detach_capture(self.item_id, self.picker.currentData())
            self._refresh_evidence()
        except ValueError as error:
            self.message.setText(str(error))

    def _commit(self):
        try:
            self.item = update_work_item(self.item_id, {'description': self.description.toPlainText()}, self.item['version'])
            return True
        except ValueError as error:
            self.message.setText(str(error))
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
            self.message.setText(str(error))

    def _snip(self):
        if not self._commit():
            return
        from .snipping import snip_into
        def done(error):
            if error:
                self.message.setText(error)
            else:
                self._refresh_evidence()
        snip_into(self.item_id, self, done)

    def _menu(self):
        if self._commit():
            self.accept()

    def _save(self):
        if self._commit():
            self.accept()
