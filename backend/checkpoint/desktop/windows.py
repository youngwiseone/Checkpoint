"""Shared native popup helpers and non-focus-stealing toast."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon, QFont, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from ..capture import win32
from . import colors
from .colors import css
from .styles import popup_css


def screen_under_mouse():
    """Select a popup's display using Qt's logical cursor coordinates."""
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def logical_point_for_physical(x: int, y: int) -> QPoint:
    """Map a physical-pixel point (win32/mss) to Qt logical coordinates on mixed-DPI setups."""
    for screen in QGuiApplication.screens():
        g = screen.geometry()
        dpr = screen.devicePixelRatio()
        nx, ny = g.x(), g.y()  # Qt keeps screen origins in native coordinates
        if nx <= x < nx + g.width() * dpr and ny <= y < ny + g.height() * dpr:
            return QPoint(int(nx + (x - nx) / dpr), int(ny + (y - ny) / dpr))
    return QPoint(x, y)


def microphone_icon() -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(colors.current().text))
    painter.setBrush(QColor(colors.current().text))
    painter.drawRoundedRect(QRectF(9, 2, 6, 12), 3, 3)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(5, 5, 14, 13), 180 * 16, 180 * 16)
    painter.drawLine(12, 18, 12, 22)
    painter.drawLine(8, 22, 16, 22)
    painter.end()
    return QIcon(pixmap)


class NoteWindow:
    """Compatibility entry point for the shared capture editor (loaded lazily)."""

    def __new__(cls, on_save, on_keep, on_discard, on_menu=lambda: None):
        from .editor import CaptureEditor
        return CaptureEditor(on_save=on_save, on_keep=on_keep,
                             on_discard=on_discard, on_menu=on_menu)


class Toast(QWidget):
    """Brief confirmation that never takes focus from the game/app."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        f = QFont()
        f.setPointSize(10)
        self.label.setFont(f)
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.level = "info"

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        from .glass import paint_glass
        c = colors.current()
        p = QPainter(self)
        paint_glass(p, self.rect().adjusted(1, 1, -1, -1), c, radius=20)
        p.setBrush({"success": c.success, "warning": c.warn, "error": c.danger}.get(self.level, c.accent))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(14, self.height() // 2 - 5, 10, 10)

    def show_message(self, level: str, message: str, work: Optional[dict] = None, duration_ms: Optional[int] = None) -> None:
        if QGuiApplication.platformName() == "wayland":
            # Wayland won't let a popup place itself; the notification daemon shows it instead.
            from .notify import notify

            if notify(level, message, duration_ms or (2600 if level != "error" else 6000)):
                return
        self.level = level
        self.label.setText(message)
        self.label.setStyleSheet(f"color:{css(colors.current().text)};")
        self.label.setFixedWidth(320)
        self.label.adjustSize()
        self.resize(self.label.width() + 50, max(44, self.label.height() + 22))
        self.label.move(34, (self.height() - self.label.height()) // 2)
        screen = screen_under_mouse()
        if screen:
            area = screen.availableGeometry()
            self.move(max(area.left(), area.right() - self.width() - 20),
                      max(area.top(), area.bottom() - self.height() - 20))
        self.show()
        win32.exclude_from_capture(int(self.winId()))
        self.update()
        self.timer.start(duration_ms or (2600 if level != "error" else 6000))
