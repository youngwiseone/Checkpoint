"""Shared translucent surfaces and frameless dialogs for the native desktop UI."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                              QHeaderView, QLabel, QLineEdit, QListView, QPlainTextEdit,
                              QPushButton, QSplitter, QTreeView, QVBoxLayout)

from . import colors
from .windows import logical_point_for_physical, popup_css
from .backdrop import capture_backdrop, paint_backdrop
from .icons import control_icon


def paint_glass(painter, rect, c, radius=22, strength=1.0):
    """Keep the desktop visible through the tint; use a light rim for definition."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(rect)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0, colors.alpha(c.surface_2, min(255, int(232 * strength))))
    gradient.setColorAt(.55, colors.alpha(c.card, min(255, int(216 * strength))))
    gradient.setColorAt(1, colors.alpha(c.surface_3, min(255, int(230 * strength))))
    painter.setBrush(gradient)
    painter.setPen(QPen(colors.alpha(c.border_strong, 95), 1))
    painter.drawRoundedRect(rect, radius, radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(colors.alpha(c.rim, 90 if c.dark else 230), 1.2))
    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
    highlight = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    highlight.setColorAt(0, colors.alpha(c.surface_2, 160))
    highlight.setColorAt(.35, colors.alpha(c.surface_2, 0))
    highlight.setColorAt(1, colors.alpha(c.border_strong, 35))
    painter.setPen(QPen(highlight, 2))
    painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), radius - 2, radius - 2)


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
        c = colors.current()
        self._theme = c.name
        self.setStyleSheet(popup_css(c))

    def content_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 24)
        layout.setSpacing(16)
        header = QHBoxLayout()
        title = QLabel(self.windowTitle())
        title.setStyleSheet('font-size: 23px; font-weight: 600; background: transparent;')
        header.addWidget(title, 1)
        close = QPushButton()
        close.setObjectName('glassCircle')
        close.setIcon(control_icon('close'))
        close.setFixedSize(44, 44)
        close.setAccessibleName('Close ' + self.windowTitle())
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)
        return layout

    def showEvent(self, event):
        center_on_screen(self)
        c = colors.current()
        # Only repolish after an actual theme change.
        if c.name != self._theme:
            self._theme = c.name
            self.setStyleSheet(popup_css(c))
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
        paint_glass(painter, self.rect().adjusted(1, 1, -1, -1), colors.current(), radius=30)
        painter.end()


class TextDialog(GlassDialog):
    def __init__(self, parent, title, label, text='', multiline=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(410, 330 if multiline else 230)
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


class DirectoryDialog(GlassDialog):
    """The Qt directory picker embedded in the same glass shell as Settings."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(780, 570)
        layout = self.content_layout()
        from ..config import paths
        self.chooser = QFileDialog(self, title, str(paths().root))
        self.chooser.setOption(QFileDialog.Option.DontUseNativeDialog)
        self.chooser.setOption(QFileDialog.Option.DontUseCustomDirectoryIcons)
        self.chooser.setFileMode(QFileDialog.FileMode.Directory)
        self.chooser.setOption(QFileDialog.Option.ShowDirsOnly)
        self.chooser.setViewMode(QFileDialog.ViewMode.Detail)
        self.chooser.setLabelText(QFileDialog.DialogLabel.Accept, 'Choose')
        self.chooser.setWindowFlags(Qt.WindowType.Widget)
        sidebar = self.chooser.findChild(QListView, 'sidebar')
        if sidebar:
            sidebar.setMinimumWidth(150)
        splitter = self.chooser.findChild(QSplitter)
        if splitter:
            splitter.setSizes([160, 540])
        tree = self.chooser.findChild(QTreeView, 'treeView')
        if tree:
            tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for column in range(1, 4):
                tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.chooser)
        self.chooser.accepted.connect(self.accept)
        self.chooser.rejected.connect(self.reject)
        buttons = self.chooser.findChild(QDialogButtonBox)
        if buttons:
            accept = buttons.button(QDialogButtonBox.StandardButton.Open)
            if accept:
                accept.setObjectName('primary')

def get_directory(parent, title):
    # Opt out of Windows' separate native dialog, so nested menus keep our shell.
    shell = DirectoryDialog(parent, title)
    dialog = shell.chooser
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
