"""Short, snapshot-based celebrations; task updates never wait on animation."""
import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient

from . import colors
from .glass import paint_glass
from .icons import paint_decision_icon


def bounded(value):
    return max(0., min(1., value))


@dataclass
class CardSnapshot:
    item_id: str
    rect: QRectF
    image: QPixmap
    caption: QRectF


class CardEffects(QObject):
    duration = 1.55

    def __init__(self, deck):
        super().__init__(deck)
        self.deck = deck
        self.cards = []
        self.status = None
        self.started = 0.
        self.timer = QTimer(self, interval=16, timeout=self._tick)

    def clear(self):
        self.timer.stop()
        self.cards.clear()
        self.status = None
        self.deck.update()

    def start(self, status, snapshots):
        self.clear()
        if status not in ('done', 'deleted') or not snapshots or not self.deck.isVisible():
            return
        self.status = status
        # Bounded work for a batch: only visible cards, with a shared particle budget.
        budget = max(18, 180 // len(snapshots))
        for snapshot in snapshots:
            rng = random.Random(snapshot.item_id)
            rect = snapshot.rect
            particles = []
            for n in range(budget):
                x, y = rng.random() * rect.width(), rng.random() * rect.height()
                radius = math.hypot((x - rect.width() / 2) / rect.width(),
                                    (y - rect.height() / 2) / rect.height())
                particles.append((x, y, rng.uniform(-140, 140),
                                  rng.uniform(-150, 35) if status == 'done' else rng.uniform(-270, -85),
                                  rng.uniform(3, 8), rng.uniform(-240, 240),
                                  rng.uniform(0, .18) if status == 'done' else radius * .95,
                                  n % 6))
            success = QPixmap(snapshot.image.size())
            success.fill(Qt.GlobalColor.transparent)
            if status == 'done':
                painter = QPainter(success)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                local = QRectF(success.rect())
                shape = QPainterPath()
                shape.addRoundedRect(local, 24, 24)
                painter.setClipPath(shape)
                frost = self.deck._frosted_preview(snapshot.image, local)
                painter.drawPixmap(local, frost, QRectF(frost.rect()))
                paint_glass(painter, local, colors.current(), radius=24, strength=.8)
                painter.fillRect(local, QColor(45, 200, 106, 22))
                # Keep the original caption legible so passing paper refracts its text.
                painter.drawPixmap(snapshot.caption, snapshot.image, snapshot.caption)
                paint_decision_icon(painter, QRectF(local.center().x() - 30, local.center().y() - 30, 60, 60), 'done')
                painter.end()
            self.cards.append((snapshot, particles, success))
        self.started = time.monotonic()
        self.timer.start()
        self.deck.update()

    def _tick(self):
        if time.monotonic() - self.started >= self.duration:
            self.clear()
        else:
            self.deck.update()

    def paint(self, painter, elapsed=None):
        if not self.cards:
            return
        elapsed = time.monotonic() - self.started if elapsed is None else elapsed
        if elapsed >= self.duration:
            return
        for snapshot, particles, success in self.cards:
            painter.save()
            painter.translate(snapshot.rect.topLeft())
            if self.status == 'done':
                self._confetti(painter, snapshot, particles, success, elapsed)
            else:
                self._burn(painter, snapshot, particles, elapsed)
            painter.restore()

    def _confetti(self, painter, snapshot, particles, success, elapsed):
        local = QRectF(snapshot.image.rect())
        card_alpha = 1 - bounded((elapsed - .75) / .4)
        painter.setOpacity(card_alpha)
        painter.drawPixmap(0, 0, success)
        # Actual pieces of the captured card peel away before becoming coloured paper.
        for x, y, vx, vy, size, spin, delay, hue in particles:
            age = elapsed - delay
            if age < 0:
                continue
            position = QPointF(x + vx * age, y + vy * age + 220 * age * age)
            alpha = 1 - bounded((elapsed - 1.1) / .45)
            painter.save()
            painter.translate(position)
            painter.rotate(spin * age)
            width = size * (.35 + .65 * abs(math.cos(age * 8 + hue)))
            piece = QRectF(-width, -size / 2, width * 2, size)
            # Repaint a displaced, rotated patch of the success card under the paper.
            # This bends the existing caption/image locally rather than moving its layout.
            if card_alpha and local.contains(position):
                painter.setOpacity(card_alpha * .65)
                patch = QRectF(-size * 1.7, -size, size * 3.4, size * 2)
                source = patch.translated(position + QPointF(math.sin(age * 9) * 5, 3))
                painter.drawPixmap(patch, success, source)
            painter.setOpacity(alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            palette = ('#35c96d', '#2488ff', '#ffcc36', '#f252a0', '#9d65e8', '#ff7353')
            painter.setBrush(QColor(palette[hue]))
            painter.drawPolygon(QPolygonF([piece.topLeft(), piece.topRight() + QPointF(1, 2),
                                          piece.bottomRight(), piece.bottomLeft() + QPointF(-1, -2)]))
            if age < .3:
                painter.setOpacity(1 - age / .3)
                source = QRectF(x - 12, y - 10, 24, 20).intersected(local)
                painter.drawPixmap(QRectF(-12, -10, 24, 20), snapshot.image, source)
            painter.restore()
        # The entire original surface dissolves into those flying fragments.
        if elapsed < .24:
            painter.setOpacity(1 - elapsed / .24)
            painter.drawPixmap(0, 0, snapshot.image)

    @staticmethod
    def burn_hole(rect, progress):
        """An irregular expanding hole: combustion starts on the face, at its centre."""
        radius = math.hypot(rect.width(), rect.height()) * .62 * bounded(progress)
        center = rect.center()
        path = QPainterPath()
        for n in range(97):
            angle = n / 96 * math.tau
            rough = 1 + .075 * math.sin(angle * 9) + .035 * math.sin(angle * 17 + 1)
            point = center + QPointF(math.cos(angle), math.sin(angle)) * radius * rough
            if n == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        path.closeSubpath()
        return path

    def _burn(self, painter, snapshot, particles, elapsed):
        local = QRectF(snapshot.image.rect())
        shape = QPainterPath()
        shape.addRoundedRect(local, 24, 24)
        progress = bounded(elapsed / .95)
        hole = self.burn_hole(local, progress)
        remaining = shape.subtracted(hole)
        painter.save()
        painter.setClipPath(remaining, Qt.ClipOperation.IntersectClip)
        painter.drawPixmap(0, 0, snapshot.image)
        painter.fillRect(local, QColor(25, 16, 12, int(progress * 145)))
        # Charred band, orange combustion rim, and a narrow hot yellow edge.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for width, color in ((38, QColor(24, 17, 14, 230)), (17, QColor(112, 41, 16, 230)),
                             (9, QColor('#f55a20')), (3, QColor('#ffe7a2'))):
            painter.setPen(QPen(color, width))
            painter.drawPath(hole)
        for n in range(30):
            angle = n * math.tau / 30
            radius = math.hypot(local.width(), local.height()) * .62 * progress
            rough = 1 + .075 * math.sin(angle * 9) + .035 * math.sin(angle * 17 + 1)
            point = local.center() + QPointF(math.cos(angle), math.sin(angle)) * radius * rough
            painter.save()
            painter.translate(point)
            painter.rotate(math.sin(n + elapsed * 13) * 22)
            height = 20 + 22 * (.5 + .5 * math.sin(n * 2 + elapsed * 18))
            flame = QPainterPath(QPointF(-8, 7))
            flame.cubicTo(-15, -8, 8, -height * .55, 2, -height)
            flame.cubicTo(22, -height * .5, 9, -7, 9, 7)
            flame.closeSubpath()
            glow = QRadialGradient(0, -7, height)
            glow.setColorAt(0, QColor('#fff0ae'))
            glow.setColorAt(.35, QColor('#ffa02c'))
            glow.setColorAt(1, QColor(255, 48, 22, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawPath(flame)
            painter.restore()
        painter.restore()
        # Sparks rise and cool into ash, leaving no opaque rectangle behind the card.
        for x, y, vx, vy, size, spin, delay, hue in particles:
            age = elapsed - delay
            if age < 0:
                continue
            alpha = (1 - bounded(age / .9)) * (1 - bounded((elapsed - 1.15) / .4))
            if alpha <= 0:
                continue
            painter.save()
            painter.setOpacity(alpha)
            painter.translate(x + vx * age * .55, y + vy * age - 35 * age * age)
            painter.rotate(spin * age)
            painter.setPen(Qt.PenStyle.NoPen)
            ember = QColor('#ffd16a') if age < .22 else QColor('#ff6b29') if age < .48 else QColor('#665953')
            painter.setBrush(ember)
            painter.drawEllipse(QRectF(-size / 3, -size / 2, size / 1.5, size))
            painter.restore()
