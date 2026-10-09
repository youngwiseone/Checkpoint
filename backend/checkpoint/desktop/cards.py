"""A small animated review deck: wheel, vertical drag, touch and click to expand."""
import time

from PySide6.QtCore import QEasingCurve, QEvent, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QWidget

from . import colors
from .glass import paint_glass


class TaskDeck(QWidget):
    navigate = Signal(int)
    decide = Signal(str)
    expand = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.item = None
        self.image = QPixmap()
        self.count = 0
        self.behind = []
        self.swipe_threshold = 90
        self.offset = 0.0
        self.hover = False
        self.press = None
        self.pointer = None
        self.wheel_delta = 0
        self.last_wheel = 0
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Task card. Swipe up to approve, down to delete, or click to expand.')
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(260)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._animate)
        self.setMinimumHeight(280)

    def _animate(self, value):
        if value is None:
            return
        self.offset = float(value)
        self.update()
        self._update_mask()

    def _update_mask(self):
        callback = getattr(self.window(), "update_glass_mask", None)
        if callback:
            callback()

    def settle(self, start=None):
        self.animation.stop()
        self.animation.setStartValue(self.offset if start is None else start)
        self.animation.setEndValue(0.0)
        self.animation.start()

    def present(self, item, image, count, direction=0, behind=None):
        changed = (self.item or {}).get('id') != (item or {}).get('id')
        self.item, self.image, self.count = item, image, count
        self.behind = behind or []
        self.setAccessibleDescription(
            f"{item['title']}. {item.get('description', '')}" if item else 'No matching tasks. Add a note or capture a screenshot.')
        if changed and direction:
            self.settle(90.0 * direction)
        self.update()

    def card_rect(self):
        width = min(300, self.width() - 100)
        height = min(400, self.height() - 36)
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2 - 4 + self.offset, width, height)

    def back_cards(self):
        rect = self.card_rect().translated(0, -self.offset)
        for layer, (item, image) in reversed(list(enumerate(self.behind[:2]))):
            dx, dy, angle = (-38, 9, -7) if layer == 0 else (38, 18, 7)
            back = rect.translated(dx, dy)
            transform = QTransform()
            transform.translate(back.center().x(), back.center().y())
            transform.rotate(angle)
            transform.translate(-back.center().x(), -back.center().y())
            yield back, transform, item, image

    def surface_path(self):
        path = QPainterPath()
        path.addRoundedRect(self.card_rect(), 24, 24)
        for rect, transform, _, _ in self.back_cards():
            back = QPainterPath()
            back.addRoundedRect(rect, 24, 24)
            path = path.united(transform.map(back))
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = colors.current()
        rect = self.card_rect()
        front_clip = QPainterPath()
        front_clip.addRoundedRect(rect, 24, 24)
        visible_backs = QPainterPath()
        visible_backs.addRect(QRectF(self.rect()))
        visible_backs = visible_backs.subtracted(front_clip)
        for back, transform, item, image in self.back_cards():
            painter.save()
            painter.setClipPath(visible_backs)
            painter.setTransform(transform, combine=True)
            self._paint_card(painter, back, item, image, c, back=True)
            painter.restore()
        self._paint_card(painter, rect, self.item, self.image, c)
        if self.press is not None and abs(self.offset) > 16 and self.item:
            decision = 'APPROVE' if self.offset < 0 else 'DELETE'
            tint = c.success if self.offset < 0 else c.danger
            painter.setPen(QPen(tint, 2))
            painter.setBrush(colors.alpha(c.card, 240))
            badge = QRectF(rect.center().x() - 68, rect.top() + 12, 136, 38)
            painter.drawRoundedRect(badge, 12, 12)
            painter.setFont(QFont('Segoe UI', 12, QFont.Weight.Bold))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, decision)
        painter.end()

    def _paint_card(self, painter, rect, item, image, c, back=False):
        painter.save()
        # A diffuse local shadow, leaving the rest of the window completely clear.
        for spread in (8, 5, 2):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.alpha(c.shadow, 10))
            painter.drawRoundedRect(rect.adjusted(-spread, spread, spread, spread * 2), 24, 24)
        paint_glass(painter, rect, c, radius=24, strength=1.03 if self.hover and not back else 1)
        if self.pointer and rect.contains(self.pointer):
            painter.setPen(QPen(colors.alpha(c.focus, 75), 2))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 22, 22)
        clip = QPainterPath()
        clip.addRoundedRect(rect.adjusted(16, 16, -16, -16), 10, 10)
        painter.setClipPath(clip)
        inner = rect.adjusted(22, 22, -22, -22)
        painter.setPen(c.text)
        if not item:
            painter.setFont(QFont('Segoe UI', 20, QFont.Weight.DemiBold))
            painter.drawText(inner.adjusted(0, 65, 0, -170), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             'Your next moment starts here')
            painter.setFont(QFont('Segoe UI', 11))
            painter.setPen(c.text_2)
            painter.drawText(inner.adjusted(0, 160, 0, -55), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             'Tap your capture key to save a screenshot, or add a note below.')
        else:
            painter.setFont(QFont('Segoe UI', 10, QFont.Weight.DemiBold))
            painter.setPen(c.focus if c.dark else c.accent)
            badge = item.get('type', 'task').replace('_', ' ').title()
            badge += '  ·  ' + item.get('work_status', 'open').replace('_', ' ').title()
            painter.drawText(QRectF(inner.x(), inner.y(), inner.width(), 24), badge)
            y = inner.y() + 32
            if not image.isNull():
                image_rect = QRectF(inner.x(), y, inner.width(), min(148, rect.height() * .36))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(colors.alpha(c.bg, 120))
                painter.drawRoundedRect(image_rect, 12, 12)
                size = image.size().scaled(image_rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
                target = QRectF(image_rect.center().x() - size.width() / 2,
                                image_rect.center().y() - size.height() / 2, size.width(), size.height())
                painter.drawPixmap(target, image, QRectF(image.rect()))
                y = image_rect.bottom() + 16
            painter.setPen(c.text)
            painter.setFont(QFont('Segoe UI', 16, QFont.Weight.DemiBold))
            title_height = min(66, painter.fontMetrics().boundingRect(
                QRect(0, 0, int(inner.width()), 1000), Qt.TextFlag.TextWordWrap, item['title']).height() + 2)
            title_rect = QRectF(inner.x(), y, inner.width(), title_height)
            painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                             item['title'])
            y = title_rect.bottom() + 10
            painter.setFont(QFont('Segoe UI', 10))
            painter.setPen(c.text_2)
            text = item.get('description') or '\n'.join(e.get('text', '') for e in item.get('evidence', []) if e['kind'] != 'capture')
            snippet = text[:240] + ('…' if len(text) > 240 else '')
            painter.save()
            line_height = painter.fontMetrics().lineSpacing()
            available = max(0, rect.bottom() - 65 - y)
            snippet_rect = QRectF(inner.x(), y, inner.width(), int(available // line_height) * line_height)
            painter.setClipRect(snippet_rect)
            painter.drawText(snippet_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, snippet)
            painter.restore()
        painter.setPen(QPen(colors.alpha(c.text, 35), 1))
        painter.drawLine(int(inner.left()), int(rect.bottom() - 52), int(inner.right()), int(rect.bottom() - 52))
        painter.setPen(c.text_2)
        painter.setFont(QFont('Segoe UI', 9))
        painter.drawText(QRectF(inner.x(), rect.bottom() - 43, inner.width(), 26),
                         Qt.AlignmentFlag.AlignCenter, '↑ Approve · ↓ Delete · Click to expand' if item else 'Screenshots and notes stay together')
        painter.restore()

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.pointer = None
        self.update()

    def begin_drag(self, point):
        if self.press is not None:
            return
        if not self.card_rect().contains(point):
            return
        self.setFocus()
        self.animation.stop()
        self.offset = 0
        self.press = point

    def drag_to(self, point):
        self.pointer = point
        if self.press is not None:
            self.offset = max(-160.0, min(160.0, point.y() - self.press.y()))
        self.update()
        self._update_mask()

    def end_drag(self, point):
        if self.press is None:
            return
        delta = point - self.press
        self.press = None
        if self.item and abs(delta.y()) >= self.swipe_threshold and abs(delta.y()) > abs(delta.x()):
            self.decide.emit('done' if delta.y() < 0 else 'deleted')
        elif delta.manhattanLength() < 8 and self.item:
            self.expand.emit()
        self.settle()

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
        if now - self.last_wheel < .24:
            event.accept()
            return
        self.wheel_delta += event.pixelDelta().y() if not event.pixelDelta().isNull() else event.angleDelta().y()
        if abs(self.wheel_delta) >= 60:
            self.navigate.emit(1 if self.wheel_delta < 0 else -1)
            self.wheel_delta = 0
            self.last_wheel = now
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.navigate.emit(1 if event.key() == Qt.Key.Key_Down else -1)
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
            self.settle()
            return True
        return super().event(event)
