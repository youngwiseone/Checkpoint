"""Native quick-note window and non-focus-stealing toast (PySide6, main thread only)."""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QPainter, QPixmap, QShortcut
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

BG = "#15171c"
SURFACE = "#1d2027"
SURFACE2 = "#262a33"
BORDER = "#343a47"
TEXT = "#e8e9ec"
MUTED = "#b0b5c1"
ACCENT = "#5b8cff"
DANGER = "#f07171"
SUCCESS = "#4cc38a"
WARN = "#e0a84e"

STYLE = f"""
QWidget#root {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel {{ color: {TEXT}; font-size: 15px; }}
QLabel#muted {{ color: {MUTED}; font-size: 13px; }}
QLabel#title {{ font-size: 16px; font-weight: 600; }}
QLabel#error {{ color: {DANGER}; font-size: 13px; }}
QPlainTextEdit {{ background: {BG}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px;
                  font-size: 15px; selection-background-color: {ACCENT}; }}
QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QComboBox {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 10px; font-size: 14px; }}
QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; color: {TEXT}; selection-background-color: {ACCENT}; }}
QPushButton {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; font-size: 14px; }}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#danger {{ color: {DANGER}; }}
"""

CATEGORIES = [("", "No category"), ("bug", "Bug"), ("improvement", "Improvement"), ("idea", "Idea"),
              ("task", "Task"), ("question", "Question"), ("note", "Note")]


def logical_point_for_physical(x: int, y: int) -> QPoint:
    """Map a physical-pixel point (win32/mss) to Qt logical coordinates on mixed-DPI setups."""
    for screen in QGuiApplication.screens():
        g = screen.geometry()
        dpr = screen.devicePixelRatio()
        nx, ny = g.x(), g.y()  # Qt keeps screen origins in native coordinates
        if nx <= x < nx + g.width() * dpr and ny <= y < ny + g.height() * dpr:
            return QPoint(int(nx + (x - nx) / dpr), int(ny + (y - ny) / dpr))
    return QPoint(x, y)


class NoteWindow(QWidget):
    """Shown when a capture needs typed context, or for an F9 text-only note."""

    def __init__(self, on_save: Callable[[dict, str, Optional[str]], Optional[str]],
                 on_keep: Callable[[dict], None], on_discard: Callable[[dict], Optional[str]]):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.on_save, self.on_keep, self.on_discard = on_save, on_keep, on_discard
        self.payload: dict = {}
        self._esc_armed = 0.0
        self.setStyleSheet(STYLE)
        root = QWidget(self)
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
        self.thumb.setFixedHeight(170)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(f"background:{BG}; border:1px solid {BORDER}; border-radius:8px;")
        self.prompt = QLabel("What should we remember about this?")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Describe what you noticed…")
        self.text.setAccessibleName("What should we remember about this?")
        self.text.setMinimumHeight(110)
        self.prompt.setBuddy(self.text)
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
    def open_for(self, payload: dict) -> None:
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
        self.adjustSize()
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
        prev = self.payload.get("fg_hwnd") or 0
        self.hide()
        if prev:
            win32.restore_foreground(prev)
        self.payload = {}

    # ------------------------------------------------------------ actions
    def _show_error(self, msg: str) -> None:
        self.error.setText(msg)
        self.error.show()
        self.adjustSize()

    def _save(self) -> None:
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
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(SURFACE))
        p.setPen(QColor(BORDER))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        color = {"success": SUCCESS, "warning": WARN, "error": DANGER}.get(self.level, ACCENT)
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(14, self.height() // 2 - 5, 10, 10)

    def show_message(self, level: str, message: str, work: Optional[dict] = None, duration_ms: Optional[int] = None) -> None:
        self.level = level
        self.label.setText(message)
        self.label.setStyleSheet(f"color:{TEXT};")
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
