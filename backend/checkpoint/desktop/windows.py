"""Native quick-note window and non-focus-stealing toast (PySide6, main thread only)."""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QFont, QGuiApplication, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..capture import win32
from . import colors
from .colors import Colors, css

RADIUS = 18  # matches the feel of the overlay's cards


def note_css(c: Colors) -> str:
    """The note window in the current theme: a night-blue card, or the light card look."""
    return f"""
QWidget#root {{ background: {css(colors.alpha(c.card, 210))}; border: 1px solid {css(c.border)}; border-radius: {RADIUS}px; }}
QLabel {{ color: {css(c.text)}; font-size: 15px; }}
QLabel#muted {{ color: {css(c.muted)}; font-size: 13px; }}
QLabel#title {{ font-size: 16px; font-weight: 600; }}
QLabel#error {{ color: {css(c.danger)}; font-size: 13px; }}
QLabel#thumb {{ background: {css(c.field)}; border: 1px solid {css(c.border)}; border-radius: 10px; }}
QPlainTextEdit {{ background: {css(c.field)}; color: {css(c.text)}; border: 1px solid {css(c.border)}; border-radius: 10px; padding: 8px;
                  font-size: 15px; selection-background-color: {css(c.accent)}; selection-color: {css(c.on_accent)}; }}
QPlainTextEdit:focus {{ border: 1px solid {css(c.accent)}; }}
QComboBox {{ background: {css(c.button)}; color: {css(c.text)}; border: 1px solid {css(c.border)}; border-radius: 10px; padding: 5px 10px;
             font-size: 14px; }}
QComboBox:focus {{ border: 1px solid {css(c.accent)}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({colors.arrow_icon(c.muted)}); width: 10px; height: 6px; margin-right: 6px; }}
QComboBox QAbstractItemView {{ background: {css(c.surface_2)}; color: {css(c.text)}; selection-background-color: {css(c.accent)};
                               selection-color: {css(c.on_accent)}; }}
QPushButton {{ background: {css(c.button)}; color: {css(c.text)}; border: 1px solid {css(c.border)}; border-radius: 10px; padding: 7px 14px;
               font-size: 14px; }}
QPushButton:hover {{ border-color: {css(c.border_strong)}; }}
QPushButton:focus {{ border: 1px solid {css(c.accent)}; }}
QPushButton#primary {{ background: {css(c.accent)}; border-color: {css(c.accent)}; color: {css(c.on_accent)}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {css(c.accent_strong)}; border-color: {css(c.accent_strong)}; }}
QPushButton#danger {{ color: {css(c.danger)}; }}
"""

CATEGORIES = [("", "No category"), ("bug", "Bug"), ("improvement", "Improvement"), ("idea", "Idea"),
              ("task", "Task"), ("question", "Question"), ("note", "Note")]


def popup_css(c: Colors) -> str:
    return note_css(c) + f"""
QWidget {{ color: {css(c.text)}; font-family: 'Segoe UI'; font-size: 14px; }}
QDialog, QFrame#inbox {{ background: transparent; border: none; }}
QTabWidget::pane, QListWidget, QFrame#activityCard, QFrame#controlPill {{ background: {css(colors.alpha(c.card, 205))}; color: {css(c.text)}; border: 1px solid {css(colors.alpha(c.focus, 75))}; border-radius: 16px; }}
QTabWidget::pane {{ padding: 18px; }}
QTabWidget::pane QWidget {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLineEdit, QSpinBox, QPlainTextEdit {{ background: {css(colors.alpha(c.field, 210))}; color: {css(c.text)}; border: 1px solid {css(c.border)}; border-radius: 10px; padding: 9px; }}
QComboBox, QPushButton {{ background: {css(colors.alpha(c.button, 205))}; }}
QPushButton:hover {{ background: {css(colors.alpha(c.selected, 205))}; border-color: {css(c.focus)}; }}
QPushButton:pressed {{ background: {css(c.selected)}; padding-top: 9px; padding-bottom: 5px; }}
QListWidget::item {{ padding: 12px; border-bottom: 1px solid {css(c.border)}; }}
QListWidget::item:selected {{ background: {css(c.selected)}; }}
QScrollBar:vertical {{ width: 7px; background: transparent; margin: 3px; }}
QScrollBar::handle:vertical {{ background: {css(c.border_strong)}; border-radius: 3px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QCheckBox {{ color: {css(c.text)}; padding: 4px; }}
QTabBar::tab {{ background: transparent; color: {css(c.muted)}; padding: 12px 16px; border-radius: 10px; margin: 3px; }}
QTabBar::tab:selected {{ background: {css(c.selected)}; color: {css(c.text)}; }}
QProgressBar {{ background: {css(c.field)}; border: 1px solid {css(c.border)}; border-radius: 5px; min-height: 10px; max-height: 10px; }}
QProgressBar::chunk {{ background: {css(c.accent)}; border-radius: 5px; }}
QPushButton:disabled {{ color: {css(c.faint)}; background: {css(c.surface)}; }}
"""


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


class NoteWindow(QWidget):
    """Shown when a capture needs typed context, or for an F9 text-only note."""

    def __init__(self, on_save: Callable[[dict, str, Optional[str]], Optional[str]],
                 on_keep: Callable[[dict], None], on_discard: Callable[[dict], Optional[str]]):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.on_save, self.on_keep, self.on_discard = on_save, on_keep, on_discard
        self.payload: dict = {}
        self._esc_armed = 0.0
        self._dictation = None
        self._dictation_save = False
        self.dictation_timer = QTimer(self, interval=100, timeout=self._poll_dictation)
        self._theme = ""
        self._apply_theme()
        root = self.root = QWidget(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        self.heading = QLabel("Screenshot saved")
        self.heading.setObjectName("title")
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedHeight(170)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prompt = QLabel("What should we remember about this?")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Describe what you noticed…")
        self.text.setAccessibleName("What should we remember about this?")
        self.text.setMinimumHeight(110)
        self.prompt.setBuddy(self.text)
        self.mic_btn = QPushButton("Dictate note")
        self.mic_btn.setIcon(microphone_icon())
        self.mic_btn.setAccessibleName("Dictate note using microphone")
        self.mic_btn.setToolTip("Record a voice note with Whistle. Click again to stop; maximum 60 seconds.")
        self.mic_btn.clicked.connect(self._toggle_dictation)
        self.dictation_status = QLabel("")
        self.dictation_status.setObjectName("muted")
        self.dictation_status.setWordWrap(True)
        row = QHBoxLayout()
        self.category = QComboBox()
        self.category.setAccessibleName("Category (optional)")
        for value, label in CATEGORIES:
            self.category.addItem(label, value)
        row.addWidget(QLabel("Category"))
        row.addWidget(self.category, 1)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        btns = QHBoxLayout()
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setObjectName("danger")
        self.keep_btn = QPushButton("Keep for later  (Esc)")
        self.save_btn = QPushButton("Save  (Ctrl+Enter)")
        self.save_btn.setObjectName("primary")
        btns.addWidget(self.discard_btn)
        btns.addStretch(1)
        btns.addWidget(self.keep_btn)
        btns.addWidget(self.save_btn)
        for w in (self.heading, self.sub, self.thumb, self.prompt, self.text):
            lay.addWidget(w)
        lay.addWidget(self.mic_btn)
        lay.addWidget(self.dictation_status)
        lay.addLayout(row)
        lay.addWidget(self.error)
        lay.addLayout(btns)
        self.setFixedWidth(440)
        self.save_btn.clicked.connect(self._save)
        self.keep_btn.clicked.connect(self._keep)
        self.discard_btn.clicked.connect(self._discard)
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, activated=self._save)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._keep)

    # ------------------------------------------------------------ show
    def _apply_theme(self) -> None:
        """Restyle when the theme changed since the window was last shown."""
        c = colors.current()
        if c.name != self._theme:
            self._theme = c.name
            self.setStyleSheet(note_css(c))

    def open_for(self, payload: dict) -> None:
        self._cancel_dictation()
        self._apply_theme()
        self.mic_btn.setIcon(microphone_icon())
        self.payload = payload
        is_capture = bool(payload.get("id"))
        self.text.clear()
        self.category.setCurrentIndex(0)
        self.error.hide()
        self._esc_armed = 0.0
        if is_capture:
            self.heading.setText("Screenshot saved")
            reason = payload.get("reason")
            self.sub.setText("No audio is recording, so add a quick note." if reason == "no_audio" else "Add context for this screenshot.")
            pix = QPixmap(payload.get("thumb_path", ""))
            if not pix.isNull():
                self.thumb.setPixmap(pix.scaled(404, 168, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.thumb.show()
            self.keep_btn.setText("Keep for later  (Esc)")
            self.discard_btn.show()
        else:
            self.heading.setText("Quick note")
            self.sub.setText("A text-only note, no screenshot.")
            self.thumb.hide()
            self.keep_btn.setText("Cancel  (Esc)")
            self.discard_btn.hide()
        self._fit()
        self._place(payload.get("work_rect"))
        self.show()
        win32.exclude_from_capture(int(self.winId()))
        self.raise_()
        self.activateWindow()
        win32.restore_foreground(int(self.winId()))
        self.text.setFocus()

    def _place(self, work: Optional[dict]) -> None:
        if work:
            br = logical_point_for_physical(work["left"] + work["width"] - 1, work["top"] + work["height"] - 1)
            tl = logical_point_for_physical(work["left"], work["top"])
            area = QRect(tl, br)
        else:
            area = QGuiApplication.primaryScreen().availableGeometry()
        x = area.right() - self.width() - 24
        y = area.bottom() - self.height() - 24
        self.move(max(area.left(), x), max(area.top(), y))

    def _close(self) -> None:
        self._cancel_dictation()
        prev = self.payload.get("fg_hwnd") or 0
        self.hide()
        if prev:
            win32.restore_foreground(prev)
        self.payload = {}

    def _toggle_dictation(self):
        if self._dictation is not None:
            self._dictation.stop()
            self.mic_btn.setEnabled(False)
            self.dictation_status.setText("Transcribing…")
            return
        from ..audio.dictation import NoteDictation
        from ..settings_store import get_settings
        from ..transcription.engine import model_installed
        if not model_installed("whistle"):
            self._show_error("Download Whistle in Settings → Transcript to dictate notes.")
            return
        self.error.hide()
        self._dictation_save = False
        self._dictation = NoteDictation(get_settings().audio.mic_device)
        self.mic_btn.setText("Stop dictation")
        self.dictation_status.setText("Opening microphone…")
        self._dictation.start()
        self.dictation_timer.start()
        self._fit()

    def _poll_dictation(self):
        recording = self._dictation
        if recording is None:
            return
        phase = recording.phase
        if phase != "done":
            self.dictation_status.setText({"starting": "Opening microphone…", "recording": "Listening… click Stop dictation when finished (up to 60 seconds).",
                                           "transcribing": "Transcribing with Whistle…"}[phase])
            if phase == "transcribing":
                self.mic_btn.setEnabled(False)
            return
        save = self._dictation_save
        self._dictation = None
        self.dictation_timer.stop()
        self.mic_btn.setEnabled(True)
        self.mic_btn.setText("Dictate note")
        self._dictation_save = False
        if recording.error:
            self.dictation_status.setText("")
            self._show_error("Couldn't dictate: " + recording.error)
            return
        if not recording.text:
            self.dictation_status.setText("No speech detected. Try again or type your note.")
            return
        # Append to the latest editor contents, preserving anything typed during recording.
        cursor = self.text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText((" " if self.text.toPlainText() and not self.text.toPlainText()[-1].isspace() else "") + recording.text)
        self.text.setTextCursor(cursor)
        self.dictation_status.setText("Dictation added. Edit it or save your note.")
        self._fit()
        if save:
            self._save()

    def _cancel_dictation(self):
        if self._dictation is not None:
            self._dictation.cancel()
        self._dictation = None
        self._dictation_save = False
        self.dictation_timer.stop()
        self.mic_btn.setEnabled(True)
        self.mic_btn.setText("Dictate note")
        self.dictation_status.setText("")

    # ------------------------------------------------------------ actions
    def _fit(self) -> None:
        """Resize to the content. The layouts keep their old size until their posted relayout runs, so flush it
        first: otherwise the window keeps the previous note's height (cutting off the thumbnail, or leaving a gap)."""
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.root.layout().activate()
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def _show_error(self, msg: str) -> None:
        self.error.setText(msg)
        self.error.show()
        self._fit()

    def _save(self) -> None:
        if self._dictation is not None:
            self._dictation_save = True
            self._dictation.stop()
            self.mic_btn.setEnabled(False)
            self.dictation_status.setText("Transcribing before saving…")
            return
        text = self.text.toPlainText()
        if not text.strip():
            self._show_error("Write a short note first — or press Esc to keep the screenshot for later.")
            return
        err = self.on_save(self.payload, text, self.category.currentData() or None)
        if err:
            self._show_error(err)
            return
        self._close()

    def _keep(self) -> None:
        if not self.payload.get("id"):
            if self.text.toPlainText().strip() and time.monotonic() - self._esc_armed > 2:
                self._esc_armed = time.monotonic()
                self._show_error("Press Esc again to discard this note.")
                return
            self._close()
            return
        self.on_keep(self.payload)
        self._close()

    def _discard(self) -> None:
        err = self.on_discard(self.payload)
        if err:
            self._show_error(err)
            return
        self._close()

    def closeEvent(self, e) -> None:  # noqa: ANN001, N802
        e.ignore()
        self._keep()


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
        c = colors.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(c.card)
        p.setPen(c.border)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.setBrush({"success": c.success, "warning": c.warn, "error": c.danger}.get(self.level, c.accent))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(14, self.height() // 2 - 5, 10, 10)

    def show_message(self, level: str, message: str, work: Optional[dict] = None, duration_ms: Optional[int] = None) -> None:
        self.level = level
        self.label.setText(message)
        self.label.setStyleSheet(f"color:{css(colors.current().text)};")
        self.label.setFixedWidth(320)
        self.label.adjustSize()
        self.resize(self.label.width() + 50, max(44, self.label.height() + 22))
        self.label.move(34, (self.height() - self.label.height()) // 2)
        area = QGuiApplication.primaryScreen().availableGeometry()
        if work:
            tl = logical_point_for_physical(work["left"], work["top"])
            br = logical_point_for_physical(work["left"] + work["width"] - 1, work["top"] + work["height"] - 1)
            area = QRect(tl, br)
        self.move(area.right() - self.width() - 20, area.bottom() - self.height() - 20)
        self.show()
        win32.exclude_from_capture(int(self.winId()))
        self.update()
        self.timer.start(duration_ms or (2600 if level != "error" else 6000))
