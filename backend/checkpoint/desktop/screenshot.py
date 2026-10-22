"""Shared screenshot workspace for quick capture and main-window edits."""
import math
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QKeySequence, QPainter,
                           QPainterPath, QPainterPathStroker, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractSpinBox, QColorDialog, QComboBox, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from . import colors
from .colors import css
from .glass import GlassDialog, get_text
from .icons import control_icon
from .windows import screen_under_mouse


@dataclass(frozen=True)
class Mark:
    tool: str
    points: tuple[QPointF, ...]
    color: str
    width: int
    text: str = ''
    font_size: int = 24


def mark_path(mark):
    path = QPainterPath()
    a, b = mark.points[0], mark.points[-1]
    rect = QRectF(a, b).normalized()
    if mark.tool == 'rectangle':
        path.addRect(rect)
    elif mark.tool == 'ellipse':
        path.addEllipse(rect)
    elif mark.tool == 'text':
        font = QFont('Segoe UI')
        font.setPixelSize(mark.font_size)
        metrics = QFontMetricsF(font)
        lines = mark.text.splitlines()
        path.addRect(QRectF(a.x(), a.y(), max((metrics.horizontalAdvance(line) for line in lines), default=0),
                           metrics.lineSpacing() * len(lines)))
    else:
        path.moveTo(a)
        for point in mark.points[1:]:
            path.lineTo(point)
        if len(mark.points) == 1:
            path.lineTo(a + QPointF(.01, 0))
        if mark.tool == 'arrow':
            angle = math.atan2(b.y() - a.y(), b.x() - a.x())
            length = max(12, mark.width * 4)
            for delta in (-.5, .5):
                path.moveTo(b)
                path.lineTo(b - QPointF(math.cos(angle + delta) * length, math.sin(angle + delta) * length))
    return path


def paint_mark(painter, mark):
    painter.save()
    color = QColor(mark.color)
    if mark.tool == 'highlighter':
        painter.setOpacity(.35)
    painter.setPen(QPen(color, mark.width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if mark.tool == 'text':
        font = QFont('Segoe UI')
        font.setPixelSize(mark.font_size)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        for index, line in enumerate(mark.text.splitlines()):
            painter.drawText(mark.points[0] + QPointF(0, metrics.ascent() + index * metrics.lineSpacing()), line)
    else:
        painter.drawPath(mark_path(mark))
    painter.restore()


class ScreenshotCanvas(QWidget):
    changed = Signal()
    text_requested = Signal(QPointF)

    def __init__(self):
        super().__init__()
        self.source = QPixmap()
        self.view = QRect()
        self.marks = []
        self.undo_stack = []
        self.redo_stack = []
        self.tool = 'pencil'
        self.color = '#ff4040'
        self.stroke_width = 3
        self.font_size = 24
        self.selection = None  # Widget coordinates; image geometry is independent.
        self.start = None
        self.draft = None
        self.erasing = False
        self.restored = False
        self.setMinimumSize(260, 220)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName('Screenshot drawing area')

    def load(self, path):
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise ValueError('Screenshot unavailable')
        self.source = pixmap
        self.view = self.source.rect()
        self.marks = []
        self.cancel_gesture()
        self.changed.emit()

    def cancel_gesture(self):
        self.start = self.selection = self.draft = None
        self.erasing = False
        self.update()

    def snapshot(self):
        return QPixmap(self.source), QRect(self.view), list(self.marks), self.restored

    def remember(self):
        self.undo_stack.append(self.snapshot())
        self.redo_stack.clear()

    def _restore(self, state):
        self.source, self.view, self.marks, self.restored = state
        self.cancel_gesture()
        self.changed.emit()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self._restore(self.redo_stack.pop())

    def image_rect(self):
        if self.source.isNull():
            return QRect()
        size = self.view.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect((self.width() - size.width()) // 2, (self.height() - size.height()) // 2,
                     size.width(), size.height())

    def point(self, position):
        rect = self.image_rect()
        return QPointF(self.view.x() + max(0, min(1, (position.x() - rect.x()) / rect.width())) * self.view.width(),
                       self.view.y() + max(0, min(1, (position.y() - rect.y()) / rect.height())) * self.view.height())

    def box(self):
        rect = self.image_rect()
        if self.selection is None or self.selection.width() < 3 or self.selection.height() < 3 or rect.isEmpty():
            raise ValueError('Drag a rectangle over the image first.')
        selected = self.selection.normalized().intersected(rect)
        a = self.point(QPointF(selected.topLeft()))
        b = self.point(QPointF(selected.right() + 1, selected.bottom() + 1))
        return round(a.x()), round(a.y()), max(1, round(b.x()) - round(a.x())), max(1, round(b.y()) - round(a.y()))

    def apply_crop(self):
        x, y, width, height = self.box()
        self.remember()
        self.view = QRect(x, y, width, height).intersected(self.view)
        self.cancel_gesture()
        self.changed.emit()

    def add_text(self, point, text):
        if text.strip():
            self.remember()
            self.marks.append(Mark('text', (point,), self.color, self.stroke_width, text, self.font_size))
            self.changed.emit()
            self.update()

    def erase(self, a, b):
        sweep = QPainterPath(a)
        sweep.lineTo(b + QPointF(.01, 0))
        stroker = QPainterPathStroker()
        stroker.setWidth(14 * self.view.width() / self.image_rect().width())
        hit = stroker.createStroke(sweep)
        removed = []
        for mark in self.marks:
            path = mark_path(mark)
            stroker.setWidth(mark.width + 2)
            target = path if mark.tool == 'text' else stroker.createStroke(path)
            if hit.intersects(target):
                removed.append(mark)
        if removed:
            if not self.erasing:
                self.remember()
                self.erasing = True
            self.marks = [mark for mark in self.marks if mark not in removed]
            self.changed.emit()
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.image_rect().contains(event.position().toPoint()):
            return
        self.start = self.point(event.position())
        if self.tool == 'text':
            point = self.start
            self.start = None
            self.text_requested.emit(point)
        elif self.tool == 'crop':
            self.selection = QRect(event.position().toPoint(), event.position().toPoint())
        elif self.tool == 'eraser':
            self.erasing = False
            self.erase(self.start, self.start)
        else:
            self.draft = Mark(self.tool, (self.start,), self.color, self.stroke_width)
        self.update()

    def mouseMoveEvent(self, event):
        if self.start is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        self._move(event.position())

    def _move(self, position):
        point = self.point(position)
        if self.tool == 'crop':
            rect = self.image_rect()
            x = rect.x() + (self.start.x() - self.view.x()) / self.view.width() * rect.width()
            y = rect.y() + (self.start.y() - self.view.y()) / self.view.height() * rect.height()
            bounded = QPointF(max(rect.left(), min(rect.right(), position.x())),
                              max(rect.top(), min(rect.bottom(), position.y())))
            self.selection = QRect(QPointF(x, y).toPoint(), bounded.toPoint()).normalized()
        elif self.tool == 'eraser':
            self.erase(self.start, point)
            self.start = point
        elif self.draft:
            points = self.draft.points + (point,) if self.tool in ('pencil', 'highlighter') else (self.start, point)
            self.draft = Mark(self.tool, points, self.color, self.stroke_width)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        self._move(event.position())
        if self.draft:
            self.remember()
            self.marks.append(self.draft)
        self.start = self.draft = None
        self.erasing = False
        self.changed.emit()
        self.update()

    def resizeEvent(self, event):
        self.cancel_gesture()
        super().resizeEvent(event)

    def render_image(self):
        image = QImage(self.view.size(), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(-self.view.x(), -self.view.y())
        painter.drawPixmap(0, 0, self.source)
        for mark in self.marks:
            paint_mark(painter, mark)
        painter.end()
        return image

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), colors.current().field)
        if self.source.isNull():
            return
        rect = self.image_rect()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / self.view.width(), rect.height() / self.view.height())
        painter.translate(-self.view.x(), -self.view.y())
        painter.drawPixmap(0, 0, self.source)
        for mark in self.marks:
            paint_mark(painter, mark)
        if self.draft:
            paint_mark(painter, self.draft)
        painter.restore()
        if self.selection:
            painter.setPen(QPen(QColor('#60a5fa'), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(96, 165, 250, 35))
            painter.drawRect(self.selection)


class ScreenshotDialog(GlassDialog):
    def __init__(self, capture_id, parent=None):
        super().__init__(parent)
        from .editor import capture_path
        from ..db import read_session
        from ..models import Capture
        self.capture_id = capture_id
        with read_session() as session:
            capture = session.get(Capture, capture_id)
            if capture is None or capture.status == 'discarded':
                raise ValueError('Screenshot not found')
            self.expected_sha = capture.sha256
        self.setWindowTitle('Edit image')
        theme = colors.current()
        self.setStyleSheet(self.styleSheet() + f'''
QPushButton[imageControl="true"], QComboBox#imageShape, QSpinBox#imageValue {{
    min-height: 20px; padding: 4px 10px; border-radius: 12px;
}}
QPushButton#imageTool:checked {{ background: {css(theme.selected)}; border: 2px solid {css(theme.accent)}; font-weight: 600; }}
QPushButton#imageClose {{ padding: 0; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; border-radius: 15px; }}
QSlider::groove:horizontal {{ height: 4px; background: {css(theme.border)}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {css(theme.accent)}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -5px 0; border-radius: 7px; background: {css(theme.accent)}; }}
''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        self.image = ScreenshotCanvas()
        self.image.load(capture_path(capture_id))
        self.tool_buttons = {}
        self.active_tool = None
        self.tool_settings = {'pencil': ('#ff4040', 3), 'highlighter': ('#ffe600', 20),
                              'shape': ('#ff4040', 3), 'text': ('#ff4040', 3)}
        tools = QHBoxLayout()
        tools.setSpacing(6)
        for key, label in (('pencil', 'Pencil'), ('highlighter', 'Highlighter'), ('eraser', 'Eraser'),
                           ('shape', 'Shapes'), ('text', 'Text'), ('crop', 'Crop')):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName('imageTool')
            button.setProperty('imageControl', True)
            button.clicked.connect(lambda checked=False, value=key: self.set_tool(value))
            self.tool_buttons[key] = button
            tools.addWidget(button)
        tools.addStretch()
        self.close_button = QPushButton()
        self.close_button.setObjectName('imageClose')
        self.close_button.setIcon(control_icon('close'))
        self.close_button.setAccessibleName('Close image editor')
        self.close_button.setToolTip('Close image editor')
        self.close_button.clicked.connect(self.reject)
        tools.addWidget(self.close_button)
        layout.addLayout(tools)
        options = QHBoxLayout()
        options.setSpacing(6)
        self.shape = QComboBox()
        self.shape.setObjectName('imageShape')
        for label, key in (('Rectangle', 'rectangle'), ('Ellipse', 'ellipse'), ('Line', 'line'), ('Arrow', 'arrow')):
            self.shape.addItem(label, key)
        self.shape.setAccessibleName('Shape')
        self.shape.currentIndexChanged.connect(lambda: self.set_tool('shape'))
        options.addWidget(self.shape)
        self.color_button = QPushButton('Color')
        self.color_button.setProperty('imageControl', True)
        self.color_button.clicked.connect(self.choose_color)
        options.addWidget(self.color_button)
        self.stroke = QSpinBox()
        self.stroke.setObjectName('imageValue')
        self.stroke.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.stroke.setFixedWidth(68)
        self.stroke.setRange(1, 80)
        self.stroke.setValue(3)
        self.stroke.setSuffix(' px')
        self.stroke.setAccessibleName('Stroke width')
        self.stroke.valueChanged.connect(lambda value: setattr(self.image, 'stroke_width', value))
        self.stroke_slider = QSlider(Qt.Orientation.Horizontal)
        self.stroke_slider.setRange(self.stroke.minimum(), self.stroke.maximum())
        self.stroke_slider.setValue(self.stroke.value())
        self.stroke_slider.setMinimumWidth(70)
        self.stroke_slider.setMaximumWidth(140)
        self.stroke_slider.setAccessibleName('Stroke width slider')
        self.stroke_slider.valueChanged.connect(self.stroke.setValue)
        self.stroke.valueChanged.connect(self.stroke_slider.setValue)
        self.width_label = QLabel('Width')
        options.addWidget(self.width_label)
        options.addWidget(self.stroke_slider)
        options.addWidget(self.stroke)
        self.font_size = QSpinBox()
        self.font_size.setObjectName('imageValue')
        self.font_size.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.font_size.setFixedWidth(76)
        self.font_size.setRange(8, 200)
        self.font_size.setValue(24)
        self.font_size.setSuffix(' px')
        self.font_size.setAccessibleName('Text size')
        self.font_size.valueChanged.connect(lambda value: setattr(self.image, 'font_size', value))
        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(self.font_size.minimum(), self.font_size.maximum())
        self.font_slider.setValue(self.font_size.value())
        self.font_slider.setMinimumWidth(70)
        self.font_slider.setMaximumWidth(140)
        self.font_slider.setAccessibleName('Text size slider')
        self.font_slider.valueChanged.connect(self.font_size.setValue)
        self.font_size.valueChanged.connect(self.font_slider.setValue)
        self.font_label = QLabel('Text size')
        options.addWidget(self.font_label)
        options.addWidget(self.font_slider)
        options.addWidget(self.font_size)
        self.crop_button = QPushButton('Apply crop')
        self.crop_button.setProperty('imageControl', True)
        self.crop_button.clicked.connect(self.apply_crop)
        options.addWidget(self.crop_button)
        options.addStretch()
        self.undo_button = QPushButton('Undo')
        self.undo_button.setProperty('imageControl', True)
        self.undo_button.clicked.connect(self.image.undo)
        options.addWidget(self.undo_button)
        self.redo_button = QPushButton('Redo')
        self.redo_button.setProperty('imageControl', True)
        self.redo_button.clicked.connect(self.image.redo)
        options.addWidget(self.redo_button)
        for label, callback in (('−', lambda: self.zoom_by(.8)), ('+', lambda: self.zoom_by(1.25)), ('Fit', self.fit)):
            button = QPushButton(label)
            button.setProperty('imageControl', True)
            button.setAccessibleName({'−': 'Zoom out', '+': 'Zoom in', 'Fit': 'Fit image to window'}[label])
            button.clicked.connect(callback)
            options.addWidget(button)
        self.zoom_label = QLabel()
        options.addWidget(self.zoom_label)
        layout.addLayout(options)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.image)
        layout.addWidget(self.scroll, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.message.setObjectName('muted')
        actions = QHBoxLayout()
        restore = QPushButton('Restore original')
        restore.setProperty('imageControl', True)
        restore.clicked.connect(self.restore_original)
        actions.addWidget(restore)
        actions.addWidget(self.message, 1)
        cancel = QPushButton('Cancel')
        cancel.setProperty('imageControl', True)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.save_button = QPushButton('Save')
        self.save_button.setProperty('imageControl', True)
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.image.changed.connect(self.refresh)
        self.image.text_requested.connect(self.add_text)
        for key, callback in (('Ctrl+Z', self.image.undo), ('Ctrl+Y', self.image.redo),
                              ('Ctrl+Shift+Z', self.image.redo), ('Ctrl+S', self._save)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
        self._last_geometry = None
        self.fit()
        self.set_tool('pencil')
        self.refresh_color()
        self.refresh()

    def place_on_screen(self):
        screen = screen_under_mouse()
        if screen:
            self.setGeometry(screen.availableGeometry())

    def set_tool(self, tool):
        if self.active_tool in self.tool_settings:
            self.tool_settings[self.active_tool] = (self.image.color, self.image.stroke_width)
        self.active_tool = tool
        self.image.cancel_gesture()
        self.image.tool = self.shape.currentData() if tool == 'shape' else tool
        for key, button in self.tool_buttons.items():
            button.setChecked(key == tool)
        self.shape.setVisible(tool == 'shape')
        for widget in (self.stroke, self.stroke_slider, self.width_label):
            widget.setVisible(tool in ('pencil', 'highlighter', 'shape'))
        for widget in (self.font_size, self.font_slider, self.font_label):
            widget.setVisible(tool == 'text')
        self.color_button.setVisible(tool not in ('eraser', 'crop'))
        self.crop_button.setVisible(tool == 'crop')
        if tool in self.tool_settings:
            self.image.color, width = self.tool_settings[tool]
            self.stroke.setValue(width)
        self.refresh_color()
        self.message.setText({'pencil': 'Draw on the image. Choose a color and stroke width.',
            'highlighter': 'Draw translucent highlights over words or areas.',
            'eraser': 'Drag over marks to remove them. Erases annotations added during this editing session.',
            'shape': 'Drag to draw the selected shape.', 'text': 'Click the image to add text.',
            'crop': 'Drag around the area to keep, then choose Apply crop. The cropped image will fill the view.'}[tool])
        self.message.setToolTip(self.message.text())
        self.refresh()

    def refresh_color(self):
        self.color_button.setText('Color')
        self.color_button.setToolTip(f'Annotation color: {self.image.color}')
        self.color_button.setStyleSheet(f'border-bottom: 4px solid {self.image.color};')

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.image.color), self, 'Annotation color')
        if color.isValid():
            self.image.color = color.name()
            self.refresh_color()

    def add_text(self, point):
        text, accepted = get_text(self, 'Add text', 'Text to place on the image', multiline=True)
        if accepted:
            self.image.add_text(point, text)

    def apply_crop(self):
        try:
            self.image.apply_crop()
            self.fit()
        except ValueError as error:
            self.message.setText(str(error))

    def restore_original(self):
        from .editor import capture_path
        try:
            path = capture_path(self.capture_id, original=True)
            if QPixmap(str(path)).isNull():
                raise ValueError('Original screenshot unavailable')
            self.image.remember()
            self.image.load(path)
            self.image.restored = True
            self.fit()
            self.refresh()
        except (ValueError, OSError) as error:
            self.message.setText(str(error))

    def fit(self):
        self.scroll.setWidgetResizable(True)
        self.image.setMinimumSize(260, 220)
        self.image.setMaximumSize(16777215, 16777215)
        self.zoom_label.clear()

    def zoom_by(self, factor):
        rect = self.image.image_rect()
        scale = max(.1, min(8, rect.width() / self.image.view.width() * factor))
        size = QSize(round(self.image.view.width() * scale), round(self.image.view.height() * scale))
        self.scroll.setWidgetResizable(False)
        self.image.setFixedSize(size)
        self.zoom_label.setText(f'{round(scale * 100)}%')

    def refresh(self):
        geometry = (self.image.source.cacheKey(), self.image.view.getRect())
        if geometry != self._last_geometry:
            self._last_geometry = geometry
            self.fit()
        self.undo_button.setEnabled(bool(self.image.undo_stack))
        self.redo_button.setEnabled(bool(self.image.redo_stack))
        self.crop_button.setEnabled(self.image.tool == 'crop' and self.image.selection is not None)
        self.save_button.setEnabled(not self.image.source.isNull())
        self.image.update()

    def _save(self):
        from ..services.cropping import save_image
        try:
            if self.image.selection is not None:
                self.image.apply_crop()
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not self.image.render_image().save(buffer, 'PNG'):
                raise OSError('Could not encode the edited screenshot')
            buffer.close()
            save_image(self.capture_id, bytes(data), expected_sha=self.expected_sha,
                       cropped=self.image.view != self.image.source.rect(), restored=self.image.restored)
            self.accept()
        except (ValueError, OSError) as error:
            self.message.setText(str(error))
