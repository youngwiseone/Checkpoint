"""Shared translucent surfaces and frameless dialogs for the native desktop UI."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                              QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout)

from . import colors
from .windows import logical_point_for_physical, popup_css
from .backdrop import capture_backdrop, paint_backdrop


def paint_glass(painter, rect, c, radius=22, strength=1.0):
    """Keep the desktop visible through the tint; use a light rim for definition."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(rect)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0, colors.alpha(c.surface_3, min(255, int(222 * strength))))
    gradient.setColorAt(.55, colors.alpha(c.card, min(255, int(205 * strength))))
    gradient.setColorAt(1, colors.alpha(c.bg, min(255, int(222 * strength))))
    painter.setBrush(gradient)
    painter.setPen(QPen(colors.alpha(c.focus if c.dark else c.border_strong, 110), 1))
    painter.drawRoundedRect(rect, radius, radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(colors.alpha(c.rim, 90 if c.dark else 170), 1))
    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)


def center_on_screen(widget, work=None):
    if work:
        point = logical_point_for_physical(work['left'] + work['width'] // 2,
                                           work['top'] + work['height'] // 2)
    elif widget.parentWidget() and widget.parentWidget().isVisible():
        point = widget.parentWidget().window().frameGeometry().center()
    else:
        point = QCursor.pos()
    screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
    if not screen:
        return
    available = screen.availableGeometry().adjusted(12, 12, -12, -12)
    widget.resize(min(widget.width(), available.width()), min(widget.height(), available.height()))
    widget.move(available.center().x() - widget.width() // 2,
                available.center().y() - widget.height() // 2)


class GlassDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(popup_css(colors.current()))

    def content_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel(self.windowTitle())
        title.setStyleSheet('font-size: 23px; font-weight: 600; background: transparent;')
        header.addWidget(title, 1)
        close = QPushButton('×')
        close.setFixedSize(34, 34)
        close.setStyleSheet('font-size: 20px; padding: 0; border-radius: 17px;')
        close.setAccessibleName('Close ' + self.windowTitle())
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)
        return layout

    def showEvent(self, event):
        center_on_screen(self)
        self.backdrop = capture_backdrop(self)
        super().showEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 64:
            handle = self.windowHandle()
            if handle:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_backdrop(painter, self)
        paint_glass(painter, self.rect().adjusted(1, 1, -1, -1), colors.current())
        painter.end()


class TextDialog(GlassDialog):
    def __init__(self, parent, title, label, text='', multiline=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(400, 310 if multiline else 200)
        layout = self.content_layout()
        layout.addWidget(QLabel(label))
        self.field = QPlainTextEdit(text) if multiline else QLineEdit(text)
        self.field.setAccessibleName(label)
        layout.addWidget(self.field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName('primary')
        layout.addWidget(buttons)
        self.field.setFocus()
        if not multiline:
            self.field.selectAll()
            self.field.returnPressed.connect(self.accept)


def get_text(parent, title, label, text='', multiline=False):
    dialog = TextDialog(parent, title, label, text, multiline)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    value = dialog.field.toPlainText() if multiline else dialog.field.text()
    return value, accepted


def get_directory(parent, title):
    # Opt out of Windows' separate native dialog, so nested menus keep our shell.
    shell = GlassDialog(parent)
    shell.setWindowTitle(title)
    shell.resize(700, 500)
    layout = shell.content_layout()
    dialog = QFileDialog(shell, title)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly)
    dialog.setWindowFlags(Qt.WindowType.Widget)
    layout.addWidget(dialog)
    dialog.accepted.connect(shell.accept)
    dialog.rejected.connect(shell.reject)
    return dialog.selectedFiles()[0] if shell.exec() == QDialog.DialogCode.Accepted and dialog.selectedFiles() else ''


def confirm(parent, title, message):
    dialog = GlassDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(430, 240)
    layout = dialog.content_layout()
    label = QLabel(message)
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    buttons.button(QDialogButtonBox.StandardButton.No).setDefault(True)
    layout.addWidget(buttons)
    return dialog.exec() == QDialog.DialogCode.Accepted
