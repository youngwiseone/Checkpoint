"""Drag a region on the live screen and append it to one existing task."""
import io
from datetime import datetime, timezone

from PIL import Image
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..capture import screen, win32
from ..services.review import get_work_item
from .windows import logical_point_for_physical


class SnippingOverlay(QWidget):
    def __init__(self, grab, done):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.grab, self.done = grab, done
        self.start = self.selection = None
        self.pixmap = QPixmap()
        self.pixmap.loadFromData(grab.png)
        region = grab.monitor['region']
        tl = logical_point_for_physical(region['left'], region['top'])
        br = logical_point_for_physical(region['left'] + region['width'] - 1, region['top'] + region['height'] - 1)
        self.setGeometry(QRect(tl, br))
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.show()
        win32.exclude_from_capture(int(self.winId()))
        self.activateWindow()
        self.raise_()

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pixmap)
        p.fillRect(self.rect(), QColor(0, 0, 0, 80))
        if self.selection:
            rect = self.selection.normalized().intersected(self.rect())
            source = QRect(int(rect.x() * self.grab.width / self.width()), int(rect.y() * self.grab.height / self.height()),
                           int(rect.width() * self.grab.width / self.width()), int(rect.height() * self.grab.height / self.height()))
            p.drawPixmap(rect, self.pixmap, source)
            p.setPen(QPen(QColor('#ffffff'), 2))
            p.drawRect(rect)
        p.setPen(QColor('#ffffff'))
        p.drawText(24, 36, 'Drag to snip · Esc to cancel')
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.selection = QRect(self.start, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        rect = QRect(self.start, event.position().toPoint()).normalized().intersected(self.rect())
        self.hide()
        if rect.width() < 3 or rect.height() < 3:
            self.done(None)
        else:
            x, y = int(rect.x() * self.grab.width / self.width()), int(rect.y() * self.grab.height / self.height())
            r, b = min(self.grab.width, round((rect.right() + 1) * self.grab.width / self.width())), min(self.grab.height, round((rect.bottom() + 1) * self.grab.height / self.height()))
            self.done((x, y, r - x, b - y))
        self.deleteLater()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.done(None)
            self.deleteLater()


def snip_into(item_id, parent, on_done):
    item = get_work_item(item_id)
    parent.hide()

    def start():
        try:
            grab = screen.grab('foreground', 'foreground_monitor')
            taken = datetime.now(timezone.utc)

            def finish(box):
                try:
                    if box:
                        from ..core import get_core
                        x, y, width, height = box
                        with Image.open(io.BytesIO(grab.png)) as source:
                            image = source.crop((x, y, x + width, y + height)).convert('RGB')
                            png, thumb = io.BytesIO(), io.BytesIO()
                            image.save(png, 'PNG')
                            image.thumbnail((480, 480))
                            image.save(thumb, 'JPEG', quality=85)
                        get_core().capture.import_image(png.getvalue(), thumb.getvalue(), width, height,
                            project_id=item['project_id'], session_id=item.get('session_id'), offset_ms=None,
                            taken_at=taken, status='saved', trigger='snip', attach_to=item_id)
                    on_done(None)
                except (ValueError, OSError) as error:
                    on_done(str(error))
                finally:
                    parent.show()
                    parent.raise_()
                    parent.activateWindow()
            parent._snipping = SnippingOverlay(grab, finish)
        except Exception as error:
            parent.show()
            on_done(str(error))
    QTimer.singleShot(160, start)
