"""Desktop frost confined to antialiased UI shapes, with no saved desktop images."""
import logging
import sys
from PIL import Image, ImageFilter
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QBrush, QGuiApplication, QImage, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

def frosted_image(image, size):
    """Gaussian blur of the actual backdrop, rather than flat transparent tint."""
    small = image.convert('RGB').resize((max(1, size[0] // 3), max(1, size[1] // 3)), Image.Resampling.LANCZOS)
    return small.filter(ImageFilter.GaussianBlur(5)).resize(size, Image.Resampling.LANCZOS)

def capture_backdrop(widget):
    if sys.platform != 'win32' or QGuiApplication.platformName() != 'windows':
        return QPixmap()
    try:
        import mss
        point = widget.mapToGlobal(QPoint())
        screen = QGuiApplication.screenAt(widget.frameGeometry().center()) or QGuiApplication.primaryScreen()
        geometry, dpr = screen.geometry(), screen.devicePixelRatio()
        area = {'left': int(geometry.x() + (point.x() - geometry.x()) * dpr),
                'top': int(geometry.y() + (point.y() - geometry.y()) * dpr),
                'width': max(1, round(widget.width() * dpr)),
                'height': max(1, round(widget.height() * dpr))}
        with mss.MSS() as capture:
            shot = capture.grab(area)
        image = frosted_image(Image.frombytes('RGB', shot.size, shot.rgb), (widget.width(), widget.height()))
        data = image.tobytes()
        qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(qimage)
    except (OSError, RuntimeError) as error:
        log.warning('Desktop frost unavailable: %s', error)
        return QPixmap()

def surface_path(widget):
    path = QPainterPath()
    deck = getattr(widget, 'deck', None)
    if deck and getattr(widget, 'switcher_backdrop', False):
        path.addRoundedRect(QRectF(widget.rect()).adjusted(10, 1, -10, -1), 28, 28)
    elif deck:
        for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if child.isHidden():
                continue
            if child is deck:
                deck_path = deck.surface_path()
                deck_path.translate(deck.x(), deck.y())
                path = path.united(deck_path)
            else:
                radius = 16 if child.objectName() in ('controlPill', 'activityCard') else 10
                child_path = QPainterPath()
                child_path.addRoundedRect(QRectF(child.geometry()), radius, radius)
                path = path.united(child_path)
    else:
        path.addRoundedRect(QRectF(widget.rect()).adjusted(1, 1, -1, -1), 22, 22)
    return path

def paint_backdrop(painter, widget):
    image = getattr(widget, 'backdrop', None)
    if image is None or image.isNull():
        return
    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(image))
    painter.drawPath(surface_path(widget))
    painter.restore()
