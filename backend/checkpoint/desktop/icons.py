"""Checkpoint branding and small, crisp icons for the Pearl Glass controls."""
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QLabel

from . import colors


LOGO_PATH = Path(__file__).resolve().parents[3] / 'icon' / 'checkpoint_logo.svg'


def _decision_paths(kind):
    body, detail = QPainterPath(), QPainterPath()
    if kind == 'done':
        body.addEllipse(QRectF(3, 3, 42, 42))
        detail.moveTo(14, 24)
        detail.lineTo(21, 31)
        detail.lineTo(34, 17)
        stroke = QPainterPathStroker()
        stroke.setWidth(3.8)
        stroke.setCurveThreshold(.01)
        stroke.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroke.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        detail = stroke.createStroke(detail)
    else:
        body.moveTo(25, 2)
        body.cubicTo(40, 12, 29, 20, 37, 23)
        body.cubicTo(38, 19, 40, 17, 40, 15)
        body.cubicTo(53, 37, 37, 46, 25, 46)
        body.cubicTo(5, 46, 4, 31, 12, 21)
        body.cubicTo(10, 32, 19, 33, 18, 24)
        body.cubicTo(17, 14, 29, 12, 25, 2)
        detail.moveTo(25, 20)
        detail.cubicTo(24, 30, 38, 34, 32, 42)
        detail.cubicTo(20, 52, 11, 38, 19, 31)
        detail.cubicTo(19, 36, 24, 36, 25, 20)
    return body, detail


def paint_decision_icon(painter, rect, kind, *, monochrome=None):
    """Shared completion seal and flame, with a single-ink cutout variant."""
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 48, rect.height() / 48)
    painter.setPen(Qt.PenStyle.NoPen)
    body, detail = _decision_paths(kind)
    if monochrome is not None:
        # Boolean paths flatten curves; perform the cutout at high precision so
        # rounded check ends stay smooth even in a large PNG.
        precision = QTransform().scale(64, 64)
        cutout = precision.map(body).subtracted(precision.map(detail))
        painter.setBrush(monochrome)
        painter.drawPath(precision.inverted()[0].map(cutout))
        painter.restore()
        return
    if kind == 'done':
        gradient = QLinearGradient(8, 4, 36, 44)
        gradient.setColorAt(0, QColor('#64de75'))
        gradient.setColorAt(1, QColor('#16964e'))
        painter.setBrush(gradient)
        painter.drawPath(body)
        painter.setBrush(QColor('white'))
        painter.drawPath(detail)
    else:
        gradient = QLinearGradient(24, 4, 24, 46)
        gradient.setColorAt(0, QColor('#ffb2a0'))
        gradient.setColorAt(.4, QColor('#ff514b'))
        gradient.setColorAt(1, QColor('#df2840'))
        painter.setBrush(gradient)
        painter.drawPath(body)
        gradient = QLinearGradient(24, 20, 24, 46)
        gradient.setColorAt(0, QColor('#ffe5a2'))
        gradient.setColorAt(1, QColor('#ff8b24'))
        painter.setBrush(gradient)
        painter.drawPath(detail)
    painter.restore()


def app_icon(status=None, ring=None):
    """Use the supplied SVG, with an optional tray status badge at every icon size."""
    logo = QIcon(str(LOGO_PATH))
    if status is None:
        return logo
    c = colors.current()
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mark = logo.pixmap(size, size)
        painter.drawPixmap((size - mark.width()) // 2, (size - mark.height()) // 2, mark)
        painter.scale(size / 64, size / 64)
        painter.setBrush(getattr(c, status))
        painter.setPen(QPen(getattr(c, ring) if ring else c.surface_2, 4))
        painter.drawEllipse(QRectF(44, 44, 18, 18))
        painter.end()
        icon.addPixmap(image)
    return icon


def app_logo(parent=None, size=32):
    """A display-only logo that stays sharp on high-DPI screens."""
    label = QLabel(parent)
    label.setFixedSize(size, size)
    label.setPixmap(app_icon().pixmap(QSize(size, size), label.devicePixelRatioF()))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setAccessibleName('Checkpoint')
    label.setToolTip('Checkpoint')
    return label


def control_icon(kind, color=None, active=False):
    c = colors.current()
    ink = color or c.text_2
    image = QPixmap(48, 48)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(ink, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    if kind == 'play':
        painter.setBrush(ink)
        painter.drawPolygon(QPolygonF([QPointF(8, 5), QPointF(19, 12), QPointF(8, 19)]))
    elif kind == 'stop':
        painter.setBrush(ink)
        painter.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
    elif kind == 'close':
        painter.drawLine(6, 6, 18, 18)
        painter.drawLine(18, 6, 6, 18)
    elif kind == 'settings':
        points = []
        for i in range(32):
            angle = i * math.pi / 16
            radius = 10 if i % 4 in (1, 2) else 7.5
            points.append(QPointF(12 + math.cos(angle) * radius, 12 + math.sin(angle) * radius))
        painter.drawPolygon(QPolygonF(points))
        painter.drawEllipse(QPointF(12, 12), 3, 3)
    elif kind == 'record':
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors.alpha(c.danger, 28) if active else colors.alpha(ink, 20))
        painter.drawEllipse(QRectF(2, 2, 20, 20))
        painter.setBrush(c.danger if active else ink)
        painter.drawEllipse(QRectF(5, 5, 14, 14))
    elif kind == 'pencil':
        painter.setBrush(ink)
        painter.drawPolygon(QPolygonF([QPointF(5, 15), QPointF(15, 5), QPointF(19, 9), QPointF(9, 19)]))
        painter.drawPolygon(QPolygonF([QPointF(4, 20), QPointF(5, 16), QPointF(8, 19)]))
        painter.setPen(QPen(c.surface_2, 1.5))
        painter.drawLine(13, 7, 17, 11)
    elif kind == 'trash':
        painter.drawLine(4, 6, 20, 6)
        painter.drawPolyline(QPolygonF([QPointF(9, 6), QPointF(9, 3), QPointF(15, 3), QPointF(15, 6)]))
        painter.drawPolyline(QPolygonF([QPointF(6, 6), QPointF(7, 21), QPointF(17, 21), QPointF(18, 6)]))
        painter.drawLine(10, 10, 10, 17)
        painter.drawLine(14, 10, 14, 17)
    elif kind == 'copy':
        painter.drawRoundedRect(QRectF(3, 3, 13, 13), 2, 2)
        painter.setBrush(c.surface_2)
        painter.drawRoundedRect(QRectF(8, 8, 13, 13), 2, 2)
    elif kind == 'folder':
        painter.setPen(QPen(c.accent_strong, 1))
        painter.setBrush(c.accent)
        painter.drawRoundedRect(QRectF(3, 4, 8, 6), 2, 2)
        painter.drawRoundedRect(QRectF(2, 7, 20, 14), 3, 3)
        painter.setPen(QPen(colors.alpha(c.on_accent, 180), 1))
        painter.drawLine(4, 9, 20, 9)
    elif kind == 'search':
        painter.drawEllipse(QRectF(3, 3, 12, 12))
        painter.drawLine(14, 14, 21, 21)
    elif kind in ('left', 'right'):
        x = 14 if kind == 'left' else 10
        tip = 8 if kind == 'left' else 16
        painter.drawPolyline(QPolygonF([QPointF(x, 5), QPointF(tip, 12), QPointF(x, 19)]))
    elif kind == 'images':
        painter.drawRoundedRect(QRectF(3, 3, 18, 18), 3, 3)
        painter.drawEllipse(QRectF(7, 7, 3, 3))
        painter.drawPolyline(QPolygonF([QPointF(4, 18), QPointF(10, 12), QPointF(14, 16), QPointF(18, 12), QPointF(21, 15)]))
    painter.end()
    return QIcon(image)
