"""Floating image cards with a snapping carousel and axis-locked decisions."""
import time
from collections import OrderedDict

from PIL import Image

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QTextLayout, QTextOption, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QWidget, QPushButton

from . import colors
from .backdrop import frosted_image
from .glass import paint_glass
from .icons import control_icon, paint_decision_icon
from .card_effects import CardEffects, CardSnapshot
from ..services.review import visible_context


class TaskDeck(QWidget):
    jump = Signal(bool)
    navigate = Signal(int)
    decide = Signal(str)
    expand = Signal()
    copy_images = Signal()
    select_cards = Signal(int, object)
    prepare_group_drag = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.oldest_button = QPushButton('Oldest', self)
        self.newest_button = QPushButton('Newest', self)
        for button, newest in ((self.oldest_button, False), (self.newest_button, True)):
            button.setFixedSize(112, 44)
            button.setIcon(control_icon('right' if newest else 'left'))
            button.clicked.connect(lambda checked=False, newest=newest: self.jump.emit(newest))
        self.edit_button = QPushButton(self)
        self.edit_button.setObjectName('glassCircle')
        self.edit_button.setFixedSize(44, 44)
        self.edit_button.setIcon(control_icon('pencil'))
        self.edit_button.setAccessibleName('Edit task')
        self.edit_button.setToolTip('Edit task and screenshot')
        self.edit_button.clicked.connect(self.expand.emit)
        self.edit_button.hide()
        self.copy_button = QPushButton(self)
        self.copy_button.setObjectName('glassCircle')
        self.copy_button.setFixedSize(44, 44)
        self.copy_button.setIcon(control_icon('copy'))
        self.copy_button.setAccessibleName('Copy task image')
        self.copy_button.setToolTip('Copy task image')
        self.copy_button.clicked.connect(self.copy_images.emit)
        self.copy_button.hide()
        self.copying = False
        self.play_button = QPushButton(self)
        self.play_button.setObjectName('glassCircle')
        self.play_button.setFixedSize(44, 44)
        self.play_button.setIcon(control_icon('play'))
        self.play_button.setAccessibleName('Play voice note')
        self.play_button.setToolTip('Play voice note')
        self.play_button.hide()
        self._caption_previews = OrderedDict()
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
        self.press_modifiers = Qt.KeyboardModifier.NoModifier
        self.selected_ids = set()
        self.selection_explicit = False
        self._dragging_ids = set()
        self.wheel_delta = 0
        self.last_wheel = 0
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Capture carousel. Shift-click to select a range, Ctrl-click to toggle cards. Drag up to mark the selection done, down to delete, sideways to browse, or use the pencil to edit.')
        self.animation = self._animation(self._animate)
        self.horizontal_animation = self._animation(self._animate_horizontal)
        self.effects = CardEffects(self)
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
        c = colors.current()
        paint_glass(painter, rect, c, radius=24)
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
        painter.setPen(QPen(c.text, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
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
        self.effects.clear()
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
            self._place_buttons()
            self.update()
            self._update_mask()

    def _animate_horizontal(self, value):
        if value is not None:
            self.horizontal_offset = float(value)
            self._place_buttons()
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

    def present(self, item, image, count, direction=0, behind=None, ordinal=0, selected_ids=None, selection_explicit=False):
        changed = (self.item or {}).get('id') != (item or {}).get('id')
        self.item, self.image, self.count = item, image, count
        self.behind = behind or []
        self.ordinal = ordinal
        self.selected_ids = set(selected_ids) if selected_ids is not None else ({item['id']} if item else set())
        self.selection_explicit = selection_explicit
        self.oldest_button.setEnabled(ordinal > 1)
        self.newest_button.setEnabled(bool(ordinal and ordinal < count))
        self._place_buttons()
        description = self._caption_text(item) if item else 'No matching captures.'
        self.setAccessibleDescription(('Completed. ' if item and item.get('work_status') == 'done' else '') + description)
        if changed:
            self.animation.stop()
            self.offset = 0.0
            if direction:
                # The selected neighbor starts exactly where it was during the drag.
                self.settle_horizontal(self.horizontal_offset + direction * self.card_spacing())
            else:
                self.horizontal_animation.stop()
                self.horizontal_offset = 0.0
        self._place_buttons()
        self.update()

    def _base_rect(self):
        height = max(120, min(540, self.height() - 72))
        width = min(320, max(100, self.width() - 170), height * .6)
        top = (self.height() - height) / 2 + 16
        return QRectF((self.width() - width) / 2, top, width, height)

    @staticmethod
    def _caption_text(item):
        return visible_context(item)

    def _caption_layout(self, rect, item, expanded=False):
        font = QFont('Segoe UI', 14, QFont.Weight.DemiBold)
        metrics = QFontMetricsF(font)
        text = self._caption_text(item).replace('\n', '\u2028')
        layout = QTextLayout(text, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        width = max(1, rect.width() - 32)
        limit = max(1, int((rect.height() * (.55 if expanded else .35) - 32) / metrics.lineSpacing()))
        if not expanded:
            limit = min(3, limit)
        layout.beginLayout()
        lines = []
        while len(lines) <= limit:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            lines.append((line.textStart(), line.textLength()))
        layout.endLayout()
        visible = [text[start:start + length].strip() for start, length in lines[:limit]]
        if len(lines) > limit and visible:
            start = lines[limit - 1][0]
            remaining = text[start:].replace('\u2028', ' ')
            visible[-1] = metrics.elidedText(remaining, Qt.TextElideMode.ElideRight, width)
        height = max(58, len(visible) * metrics.lineSpacing() + 32)
        panel = QRectF(rect.x(), rect.bottom() - height, rect.width(), height)
        return panel, visible, font, metrics.lineSpacing()

    def focused_title_rect(self):
        return self._caption_layout(self.card_rect(), self.item or {})[0]

    def _frosted_preview(self, image, rect):
        key = (image.cacheKey(), int(rect.width()), int(rect.height()))
        if key not in self._caption_previews:
            source = self.image_source_rect(image, rect).toAlignedRect()
            preview = image.copy(source).scaled(key[1], key[2], Qt.AspectRatioMode.IgnoreAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation)
            qimage = preview.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            pixels = Image.frombytes('RGBA', (qimage.width(), qimage.height()), bytes(qimage.constBits()),
                                     'raw', 'RGBA', qimage.bytesPerLine())
            blurred = frosted_image(pixels, (key[1], key[2]))
            data = blurred.tobytes()
            self._caption_previews[key] = QPixmap.fromImage(QImage(data, key[1], key[2], key[1] * 3,
                                                                  QImage.Format.Format_RGB888).copy())
        self._caption_previews.move_to_end(key)
        while len(self._caption_previews) > 16:
            self._caption_previews.popitem(last=False)
        return self._caption_previews[key]

    def card_spacing(self):
        return self._base_rect().width() * .76

    def card_rect(self):
        offset = self.offset if not self._dragging_ids or (self.item or {}).get('id') in self._dragging_ids else 0
        return self._base_rect().translated(self.horizontal_offset, offset)

    def neighbors(self):
        for index, entry in enumerate(self.behind):
            if len(entry) == 3:
                yield entry
            else:
                yield index + 1, entry[0], entry[1]

    def _neighbor_rect(self, relative):
        rect = self._base_rect()
        rect = rect.adjusted(0, rect.height() * .025, 0, -rect.height() * .025)
        item = next((item for n, item, _ in self.neighbors() if n == relative), {})
        offset = self.offset if item.get('id') in self._dragging_ids else 0
        return rect.translated(relative * self.card_spacing() + self.horizontal_offset, offset)

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

    def animate_decision(self, status, item_ids):
        """Capture visible changed cards before the refreshed deck replaces them."""
        if status not in ('done', 'deleted'):
            self.effects.clear()
            return
        snapshots = []
        entries = [(rect, item, image, True) for rect, _, item, image in self.back_cards()]
        if self.item:
            entries.append((self.card_rect(), self.item, self.image, False))
        for rect, item, image, back in entries:
            if item['id'] not in item_ids or not rect.intersects(QRectF(self.rect())):
                continue
            snapshot = QPixmap(rect.size().toSize())
            snapshot.fill(Qt.GlobalColor.transparent)
            painter = QPainter(snapshot)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(-rect.topLeft())
            painter.setClipRect(rect)
            self._paint_card(painter, rect, item, image, colors.current(), back=back)
            painter.end()
            expanded = not back and self.pointer is not None and rect.contains(self.pointer)
            caption = self._caption_layout(rect, item, expanded)[0].translated(-rect.topLeft())
            snapshots.append(CardSnapshot(item['id'], QRectF(rect), snapshot, caption))
        self.effects.start(status, snapshots)

    def paintEvent(self, event):
        if not self.item and not self.processing_message and not self.effects.cards:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = colors.current()
        if self.processing_message:
            self._paint_loading(painter)
        if not self.item:
            self.effects.paint(painter)
            painter.end()
            return
        # Draw the farthest cards first. Occlude their labels under the center label.
        rect = self.card_rect()
        clip = QPainterPath()
        clip.addRect(QRectF(self.rect()))
        front = QPainterPath()
        front.addRoundedRect(rect, 24, 24)
        front.addRect(QRectF(rect.center().x() - 48, rect.top() - 42, 96, 34))
        for back, _, item, image in self.back_cards():
            painter.save()
            painter.setClipPath(clip.subtracted(front))
            relative = next((n for n, candidate, _ in self.neighbors() if candidate['id'] == item['id']), 1)
            painter.setOpacity(.32 if abs(relative) == 3 else .14 if abs(relative) >= 4 else 1)
            self._paint_card(painter, back, item, image, c, back=True)
            painter.restore()
        self._paint_card(painter, rect, self.item, self.image, c)
        if self.axis == 'vertical' and abs(self.offset) > 16:
            decision = 'done' if self.offset < 0 else 'deleted'
            tint = c.success if self.offset < 0 else c.danger
            anchor = rect if (self.item or {}).get('id') in self._dragging_ids else self._neighbor_rect(self.pressed_card)
            width = 92 if len(self._dragging_ids) > 1 else 60
            badge = QRectF(anchor.center().x() - width / 2, anchor.top() + 14, width, 60)
            painter.setPen(QPen(tint, 2))
            painter.setBrush(colors.alpha(c.card, 240))
            painter.drawRoundedRect(badge, 12, 12)
            painter.setOpacity(min(1., abs(self.offset) / self.swipe_threshold))
            paint_decision_icon(painter, QRectF(badge.left() + 6, badge.top() + 6, 48, 48), decision)
            if len(self._dragging_ids) > 1:
                painter.setPen(tint)
                painter.setFont(QFont('Segoe UI', 12, QFont.Weight.Bold))
                painter.drawText(badge.adjusted(54, 0, -6, 0), Qt.AlignmentFlag.AlignCenter, str(len(self._dragging_ids)))
            painter.setOpacity(1)
        self.effects.paint(painter)
        painter.end()

    def _place_buttons(self):
        y = int(self._base_rect().center().y()) - 22
        self.oldest_button.move(4, y)
        self.newest_button.move(self.width() - 116, y)
        rect = self.card_rect()
        self.edit_button.move(int(rect.right() - 52), int(rect.top() + 8))
        self.edit_button.setVisible(bool(self.item) and self.press is None)
        self.edit_button.raise_()
        self.copy_button.move(int(rect.right() - 104), int(rect.top() + 8))
        self.copy_button.setVisible(bool(self.item) and self.press is None)
        self.copy_button.setEnabled(bool(self.selected_ids) and not self.copying)
        label = 'Copying task images…' if self.copying else (f'Copy {len(self.selected_ids)} task images' if len(self.selected_ids) > 1 else 'Copy task image')
        self.copy_button.setAccessibleName(label)
        self.copy_button.setToolTip(label)
        self.copy_button.raise_()
        self.play_button.move(int(rect.right() - 156), int(rect.top() + 8))
        self.play_button.setVisible(bool(self.item) and self.press is None and any(e.get('audio_url') for e in (self.item or {}).get('evidence', [])))
        self.play_button.raise_()

    def resizeEvent(self, event):
        self.effects.clear()
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
        for spread in (8, 4, 2):
            painter.setBrush(QColor(24, 34, 50, 12))
            painter.drawRoundedRect(rect.adjusted(-spread, spread, spread, spread * 2), 24, 24)
        if not back and self.ordinal:
            text = f'{self.ordinal} / {self.count}'
            if self.selection_explicit:
                text += f' · {len(self.selected_ids)} selected'
            width = max(84, QFontMetricsF(QFont('Segoe UI', 10)).horizontalAdvance(text) + 36)
            counter = QRectF(rect.center().x() - width / 2, rect.top() - 40, width, 30)
            paint_glass(painter, counter, c, radius=15)
            painter.setPen(c.text)
            painter.setFont(QFont('Segoe UI', 10, QFont.Weight.DemiBold))
            painter.drawText(counter, Qt.AlignmentFlag.AlignCenter, text)
        painter.save()
        shape = QPainterPath()
        shape.addRoundedRect(rect, 24, 24)
        painter.setClipPath(shape, Qt.ClipOperation.IntersectClip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c.card)
        painter.drawRect(rect)
        if not image.isNull():
            painter.drawPixmap(rect, image, self.image_source_rect(image, rect))
        elif self.preview_loading and any(e['kind'] == 'capture' for e in item.get('evidence', [])):
            painter.setPen(QPen(c.text, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(rect.center().x() - 16, rect.center().y() - 16, 32, 32),
                            self.spinner_angle * 16, 260 * 16)
        expanded = not back and self.pointer is not None and rect.contains(self.pointer)
        panel, lines, font, spacing = self._caption_layout(rect, item, expanded)
        if lines:
            painter.save()
            painter.setClipRect(panel, Qt.ClipOperation.IntersectClip)
            if not image.isNull():
                preview = self._frosted_preview(image, rect)
                painter.drawPixmap(rect, preview, QRectF(preview.rect()))
            paint_glass(painter, panel, c, radius=16, strength=1.04)
            painter.setPen(c.text)
            painter.setFont(font)
            for index, text in enumerate(lines):
                line = QRectF(panel.left() + 16, panel.top() + 16 + index * spacing,
                              panel.width() - 32, spacing)
                painter.drawText(line, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            painter.restore()
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(colors.alpha(c.surface_2, 220), 2))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 23, 23)
        if self.selection_explicit and item['id'] in self.selected_ids:
            painter.setPen(QPen(c.accent, 3))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 22, 22)
            badge = QRectF(rect.left() + 10, rect.top() + 10, 26, 26)
            painter.setBrush(c.accent)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(badge)
            painter.setPen(QPen(c.on_accent, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(badge.topLeft() + QPointF(6, 13), badge.topLeft() + QPointF(11, 18))
            painter.drawLine(badge.topLeft() + QPointF(11, 18), badge.topLeft() + QPointF(20, 8))
        visible = rect.intersected(QRectF(self.rect()))
        if item.get('work_status') == 'done' and visible.width() >= 48:
            # Keep completion separate from the existing blue selection marker.
            top = rect.top() + (44 if self.selection_explicit and item['id'] in self.selected_ids else 10)
            left = visible.right() - 48 if back and rect.center().x() > self.card_rect().center().x() else visible.left() + 10
            badge = QRectF(left, top, 38, 38)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.alpha(c.card, 230))
            painter.drawEllipse(badge)
            paint_decision_icon(painter, badge.adjusted(3, 3, -3, -3), 'done')
        painter.restore()

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.pointer = None
        self.update()

    def _hit_card(self, point):
        if self.item and self.card_rect().contains(point):
            return 0
        # Paint order establishes which exposed card receives a click.
        for relative, _, _ in sorted(self.neighbors(), key=lambda entry: abs(entry[0])):
            if self._neighbor_rect(relative).contains(point):
                return relative
        return None

    def begin_drag(self, point, modifiers=Qt.KeyboardModifier.NoModifier):
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
        self.press_modifiers = modifiers
        self.pressed_card = hit
        self._dragging_ids.clear()
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
            elif self.axis == 'vertical':
                if not self._dragging_ids:
                    self.prepare_group_drag.emit(self.pressed_card)
                    item = self.item if self.pressed_card == 0 else next(
                        (item for n, item, _ in self.neighbors() if n == self.pressed_card), None)
                    self._dragging_ids = set(self.selected_ids) if item and item['id'] in self.selected_ids else ({item['id']} if item else set())
                self.offset = max(-200.0, min(200.0, delta.y()))
        self.update()
        self._place_buttons()
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
        elif axis == 'vertical' and self.item and abs(delta.y()) >= self.swipe_threshold:
            self.decide.emit('done' if delta.y() < 0 else 'deleted')
        elif delta.manhattanLength() < 8 and self.item:
            if self.press_modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                self.select_cards.emit(self.pressed_card, self.press_modifiers)
            elif self.selection_explicit:
                item = self.item if self.pressed_card == 0 else next(
                    (item for n, item, _ in self.neighbors() if n == self.pressed_card), None)
                if item and item['id'] not in self.selected_ids:
                    self.select_cards.emit(self.pressed_card, Qt.KeyboardModifier.NoModifier)
                elif self.pressed_card:
                    self.navigate.emit(self.pressed_card)
            elif self.pressed_card:
                self.navigate.emit(self.pressed_card)
                self.settle_horizontal()
            else:
                self.expand.emit()
        self.settle()
        self._place_buttons()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin_drag(event.position(), event.modifiers())

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
            direction = 1 if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Right) else -1
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.select_cards.emit(direction, event.modifiers())
            else:
                self.navigate.emit(direction)
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
