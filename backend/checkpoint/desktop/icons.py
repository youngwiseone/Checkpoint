"""Small, crisp icons shared by the Pearl Glass controls."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap, QPolygonF

from . import colors


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
    if kind == 'close':
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
