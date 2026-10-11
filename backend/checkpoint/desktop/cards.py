"""Floating image cards with a snapping carousel and axis-locked decisions."""
import time

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QTextLayout, QTextOption, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QWidget, QPushButton

from . import colors
from ..services.review import visible_context


class TaskDeck(QWidget):
    jump = Signal(bool)
    navigate = Signal(int)
    decide = Signal(str)
    expand = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.oldest_button = QPushButton('Oldest', self)
        self.newest_button = QPushButton('Newest', self)
        for button, newest in ((self.oldest_button, False), (self.newest_button, True)):
            button.setFixedSize(84, 34)
            button.clicked.connect(lambda checked=False, newest=newest: self.jump.emit(newest))
        self.item = None
        self.image = QPixmap()
        self.count = 0
        self.ordinal = 0
        self.processing_message = ''
        self.preview_loading = False
        self.spinner_angle = 0
        self.spinner = QTimer(self, interval=60, timeout=self._spin)
        self.behind = []
        self.swipe_threshold = 90
        self.offset = 0.0
        self.horizontal_offset = 0.0
        self.hover = False
        self.press = None
        self.pointer = None
        self.axis = None
        self.pressed_card = 0
        self.wheel_delta = 0
        self.last_wheel = 0
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Capture carousel. Drag sideways to browse, up to mark done, down to delete, or click to edit.')
        self.animation = self._animation(self._animate)
        self.horizontal_animation = self._animation(self._animate_horizontal)
        self.setMinimumHeight(280)

    def set_processing(self, message=''):
        self.processing_message = message
        if message and self.isVisible():
            self.spinner.start()
        else:
            self.spinner.stop()
        self.update()

    def _spin(self):
        self.spinner_angle = (self.spinner_angle + 18) % 360
        self.update()

    def _loading_rect(self):
        if not self.item:
            return self._base_rect()
        relatives = [entry[0] for entry in self.neighbors()]
        slot = next((n for n in (1, -1, 2, -2) if n not in relatives), max([0] + relatives) + 1)
        return self._neighbor_rect(slot)

    def _paint_loading(self, painter):
        rect = self._loading_rect()
        painter.save()
        painter.setPen(QPen(QColor(255, 255, 255, 65), 1))
        painter.setBrush(QColor(18, 28, 44, 215))
        painter.drawRoundedRect(rect, 24, 24)
        content = rect.adjusted(18, 0, -18, 0)
        if self.item:
            # Keep the label inside the exposed portion of a side loading card.
            occupied = [self.card_rect()] + [self._neighbor_rect(n) for n, _, _ in self.neighbors()]
            for other in occupied:
                if other.left() <= rect.left() <= other.right():
                    content.setLeft(max(content.left(), other.right() + 14))
                if other.left() <= rect.right() <= other.right():
                    content.setRight(min(content.right(), other.left() - 14))
        spinner = QRectF(content.center().x() - 18, rect.center().y() - 50, 36, 36)
        painter.setPen(QPen(QColor('white'), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(spinner, self.spinner_angle * 16, 260 * 16)
        painter.setFont(QFont('Segoe UI', 11))
        painter.drawText(QRectF(content.left(), rect.top() + rect.height() * .52, content.width(), rect.height() * .48 - 24),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                         self.processing_message)
        painter.restore()

    def showEvent(self, event):
        if self.processing_message:
            self.spinner.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.spinner.stop()
        super().hideEvent(event)

    def _animation(self, callback):
        animation = QVariantAnimation(self)
        animation.setDuration(260)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(callback)
        return animation

    def _animate(self, value):
        if value is not None:
            self.offset = float(value)
            self.update()
            self._update_mask()

    def _animate_horizontal(self, value):
        if value is not None:
            self.horizontal_offset = float(value)
            self.update()
            self._update_mask()

    def _update_mask(self):
        callback = getattr(self.window(), 'update_glass_mask', None)
        if callback:
            callback()

    def settle(self, start=None):
        self.animation.stop()
        self.animation.setStartValue(self.offset if start is None else start)
        self.animation.setEndValue(0.0)
        self.animation.start()

    def settle_horizontal(self, start=None):
        self.horizontal_animation.stop()
        self.horizontal_animation.setStartValue(self.horizontal_offset if start is None else start)
        self.horizontal_animation.setEndValue(0.0)
        self.horizontal_animation.start()

    def present(self, item, image, count, direction=0, behind=None, ordinal=0):
        changed = (self.item or {}).get('id') != (item or {}).get('id')
        self.item, self.image, self.count = item, image, count
        self.behind = behind or []
        self.ordinal = ordinal
        self.oldest_button.setEnabled(ordinal > 1)
        self.newest_button.setEnabled(bool(ordinal and ordinal < count))
        self._place_buttons()
        self.setAccessibleDescription(f"{item['title']}. {item.get('description', '')}" if item else 'No matching captures.')
        if changed:
            self.animation.stop()
            self.offset = 0.0
            if direction:
                # The selected neighbor starts exactly where it was during the drag.
                self.settle_horizontal(self.horizontal_offset + direction * self.card_spacing())
            else:
                self.horizontal_animation.stop()
                self.horizontal_offset = 0.0
        self.update()

    def _title_layout(self):
        width = min(560, max(120, self.width() - 70))
        layout = QTextLayout((self.item or {}).get('title', ''), QFont('Segoe UI', 11, QFont.Weight.DemiBold))
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        return layout, width, max(24, height + 4)

    def _title_size(self):
        _, width, height = self._title_layout()
        return width, height

    def _base_rect(self):
        _, title_height = self._title_size()
        header = title_height + 38
        height = max(120, min(540, self.height() - header - 42))
        width = min(320, self.width() - 90, height * .64)
        top = (self.height() - height - header - 34) / 2 + header
        return QRectF((self.width() - width) / 2, top, width, height)

    def focused_title_rect(self):
        rect = self.card_rect()
        width, height = self._title_size()
        return QRectF(rect.center().x() - width / 2, rect.top() - height - 8, width, height)

    def card_spacing(self):
        return self._base_rect().width() * .76

    def card_rect(self):
        return self._base_rect().translated(self.horizontal_offset, self.offset)

    def neighbors(self):
        for index, entry in enumerate(self.behind):
            if len(entry) == 3:
                yield entry
            else:
                yield index + 1, entry[0], entry[1]

    def _neighbor_rect(self, relative):
        rect = self._base_rect()
        rect = rect.adjusted(0, rect.height() * .025, 0, -rect.height() * .025)
        return rect.translated(relative * self.card_spacing() + self.horizontal_offset, 0)

    def back_cards(self):
        for relative, item, image in sorted(self.neighbors(), key=lambda entry: abs(entry[0]), reverse=True):
            yield self._neighbor_rect(relative), QTransform(), item, image

    def surface_path(self):
        path = QPainterPath()
        if self.item:
            path.addRoundedRect(self.card_rect(), 24, 24)
            for rect, _, _, _ in self.back_cards():
                path.addRoundedRect(rect, 24, 24)
        return path

    def paintEvent(self, event):
        if not self.item and not self.processing_message:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = colors.current()
        if self.processing_message:
            self._paint_loading(painter)
        if not self.item:
            painter.end()
            return
        # Draw the farthest cards first. Occlude their labels under the center label.
        rect = self.card_rect()
        clip = QPainterPath()
        clip.addRect(QRectF(self.rect()))
        front = QPainterPath()
        front.addRoundedRect(rect, 24, 24)
        title = self.focused_title_rect()
        front.addRect(title.adjusted(-6, -26, 6, 8))
        front.addRect(QRectF(rect.x() - 3, rect.bottom(), rect.width() + 6, 34))
        for back, _, item, image in self.back_cards():
            painter.save()
            painter.setClipPath(clip.subtracted(front))
            relative = next((n for n, candidate, _ in self.neighbors() if candidate['id'] == item['id']), 1)
            painter.setOpacity(.32 if abs(relative) == 3 else .14 if abs(relative) >= 4 else 1)
            self._paint_card(painter, back, item, image, c, back=True)
            painter.restore()
        self._paint_card(painter, rect, self.item, self.image, c)
        if self.axis == 'vertical' and abs(self.offset) > 16:
            decision = 'DONE' if self.offset < 0 else 'DELETED'
            tint = c.success if self.offset < 0 else c.danger
            badge = QRectF(rect.center().x() - 68, rect.top() + 14, 136, 38)
            painter.setPen(QPen(tint, 2))
            painter.setBrush(colors.alpha(c.card, 240))
            painter.drawRoundedRect(badge, 12, 12)
            painter.setFont(QFont('Segoe UI', 12, QFont.Weight.Bold))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, decision)
        painter.setPen(QColor(255, 255, 255, 190))
        painter.setFont(QFont('Segoe UI', 10))
        painter.end()

    def _place_buttons(self):
        y = int(self._base_rect().center().y()) - 17
        self.oldest_button.move(4, y)
        self.newest_button.move(self.width() - 88, y)

    def resizeEvent(self, event):
        self._place_buttons()
        super().resizeEvent(event)

    @staticmethod
    def image_source_rect(image, target):
        """Center crop only the painted preview, leaving the original pixmap intact."""
        source = QRectF(image.rect())
        ratio = target.width() / target.height()
        if source.width() / source.height() > ratio:
            width = source.height() * ratio
            source.setX((image.width() - width) / 2)
            source.setWidth(width)
        else:
            height = source.width() / ratio
            source.setY((image.height() - height) / 2)
            source.setHeight(height)
        return source

    def _paint_card(self, painter, rect, item, image, c, back=False):
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for spread in (10, 6, 2):
            painter.setBrush(QColor(0, 0, 0, 14))
            painter.drawRoundedRect(rect.adjusted(-spread, spread, spread, spread * 2), 24, 24)
        painter.setFont(QFont('Segoe UI', 11, QFont.Weight.DemiBold))
        painter.setPen(QColor('white'))
        if back:
            heading = painter.fontMetrics().elidedText(item['title'], Qt.TextElideMode.ElideRight, int(rect.width()))
            painter.drawText(QRectF(rect.x(), rect.y() - 31, rect.width(), 24), Qt.AlignmentFlag.AlignCenter, heading)
        else:
            title = self.focused_title_rect()
            layout, _, _ = self._title_layout()
            layout.draw(painter, title.topLeft())
            if self.ordinal:
                painter.setFont(QFont('Segoe UI', 10))
                painter.drawText(QRectF(title.x(), title.y() - 26, title.width(), 22), Qt.AlignmentFlag.AlignCenter,
                                 f'{self.ordinal}/{self.count}')
        painter.setFont(QFont('Segoe UI', 10))
        painter.drawText(QRectF(rect.x(), rect.bottom() + 7, rect.width(), 23), Qt.AlignmentFlag.AlignCenter,
                         'Click to focus' if back else 'Click to edit')
        shape = QPainterPath()
        shape.addRoundedRect(rect, 24, 24)
        painter.setClipPath(shape)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c.card)
        painter.drawRect(rect)
        if not image.isNull():
            painter.drawPixmap(rect, image, self.image_source_rect(image, rect))
        elif self.preview_loading and any(e['kind'] == 'capture' for e in item.get('evidence', [])):
            painter.setPen(QPen(c.text, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(rect.center().x() - 16, rect.center().y() - 16, 32, 32), self.spinner_angle * 16, 260 * 16)
        text = visible_context(item)
        if text:
            painter.setFont(QFont('Segoe UI', 10))
            inset = 16
            width = rect.width() - inset * 2
            line = painter.fontMetrics().lineSpacing()
            expanded = not back and self.pointer is not None and rect.contains(self.pointer)
            needed = painter.fontMetrics().boundingRect(QRect(0, 0, int(width), 10000), Qt.TextFlag.TextWordWrap, text).height()
            limit = rect.height() * .55 if expanded else min(rect.height() * .35, line * 2 + inset * 2)
            note_height = min(limit, needed + inset * 2)
            if image.isNull():
                note_height = min(rect.height(), max(note_height, rect.height() * .7))
            panel = QRectF(rect.x(), rect.bottom() - note_height, rect.width(), note_height)
            painter.setBrush(QColor(10, 18, 28, 190))
            painter.drawRect(panel)
            painter.setPen(QColor('white'))
            lines = max(1, int((note_height - inset * 2) // line))
            note = panel.adjusted(inset, inset, -inset, -inset)
            if expanded or image.isNull():
                painter.drawText(note, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)
            else:
                # A compact two-line excerpt; the complete note opens on hover/edit.
                chars = max(24, int(width / painter.fontMetrics().averageCharWidth()) * lines)
                snippet = text[:chars].rstrip() + ('…' if len(text) > chars else '')
                painter.drawText(note, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, snippet)
        painter.restore()

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.pointer = None
        self.update()

    def _hit_card(self, point):
        if self.item and self.card_rect().adjusted(0, 0, 0, 32).contains(point):
            return 0
        # Paint order establishes which exposed card receives a click.
        for relative, _, _ in sorted(self.neighbors(), key=lambda entry: abs(entry[0])):
            if self._neighbor_rect(relative).adjusted(0, 0, 0, 32).contains(point):
                return relative
        return None

    def begin_drag(self, point):
        if self.press is not None:
            return
        hit = self._hit_card(point)
        if hit is None:
            return
        self.setFocus()
        self.animation.stop()
        self.horizontal_animation.stop()
        self.offset = 0.0
        self.drag_start_x = self.horizontal_offset
        self.press = point
        self.pressed_card = hit
        self.axis = None

    def drag_to(self, point):
        self.pointer = point
        if self.press is not None:
            delta = point - self.press
            if self.axis is None and max(abs(delta.x()), abs(delta.y())) >= 10:
                self.axis = 'horizontal' if abs(delta.x()) > abs(delta.y()) else 'vertical'
            if self.axis == 'horizontal':
                relatives = [0] + [entry[0] for entry in self.neighbors()]
                spacing = self.card_spacing()
                low, high = -max(relatives) * spacing, -min(relatives) * spacing
                proposed = self.drag_start_x + delta.x()
                # Elastic resistance at the first and last card.
                self.horizontal_offset = max(low, min(high, proposed))
                self.horizontal_offset += (proposed - self.horizontal_offset) * .22
            elif self.axis == 'vertical' and self.pressed_card == 0:
                self.offset = max(-200.0, min(200.0, delta.y()))
        self.update()
        self._update_mask()

    def end_drag(self, point):
        if self.press is None:
            return
        self.drag_to(point)
        delta = point - self.press
        axis = self.axis
        self.press = None
        self.axis = None
        if axis == 'horizontal':
            relatives = [0] + [entry[0] for entry in self.neighbors()]
            target = -self.horizontal_offset / self.card_spacing()
            step = min(relatives, key=lambda relative: abs(relative - target))
            if abs(delta.x()) >= max(42, self.card_spacing() * .25) and step:
                self.navigate.emit(step)
                self.settle_horizontal()
            else:
                self.settle_horizontal()
        elif axis == 'vertical' and self.pressed_card == 0 and self.item and abs(delta.y()) >= self.swipe_threshold:
            self.decide.emit('done' if delta.y() < 0 else 'deleted')
        elif delta.manhattanLength() < 8 and self.item:
            if self.pressed_card:
                self.navigate.emit(self.pressed_card)
                self.settle_horizontal()
            else:
                self.expand.emit()
        self.settle()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin_drag(event.position())

    def mouseMoveEvent(self, event):
        self.drag_to(event.position())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.end_drag(event.position())

    def wheelEvent(self, event):
        now = time.monotonic()
        delta = event.pixelDelta() if not event.pixelDelta().isNull() else event.angleDelta()
        if now - self.last_wheel > .3:
            self.wheel_delta = 0
        self.last_wheel = now
        self.wheel_delta += delta.y() if delta.y() else delta.x()
        threshold = 40 if not event.pixelDelta().isNull() else 120
        if abs(self.wheel_delta) >= threshold:
            steps = int(self.wheel_delta / threshold)
            self.wheel_delta -= steps * threshold
            self.navigate.emit(-steps)
            self.last_wheel = now
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.navigate.emit(1 if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Right) else -1)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self.item:
                self.expand.emit()
        else:
            super().keyPressEvent(event)

    def event(self, event):
        if event.type() in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd):
            points = event.points()
            if points:
                if event.type() == QEvent.Type.TouchBegin:
                    self.begin_drag(points[0].position())
                elif event.type() == QEvent.Type.TouchUpdate:
                    self.drag_to(points[0].position())
                else:
                    self.end_drag(points[0].position())
            event.accept()
            return True
        if event.type() == QEvent.Type.TouchCancel:
            self.press = None
            self.axis = None
            self.settle()
            self.settle_horizontal()
            return True
        return super().event(event)
