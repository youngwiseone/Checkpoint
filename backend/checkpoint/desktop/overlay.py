"""The review overlay (Ctrl+F9): a floating stack of cards beside whatever you're testing.

The window is transparent apart from the cards, so it never covers the whole screen and clicks
outside the cards reach the app underneath. The front card shows its screenshot fading into
the card; the next cards wait behind it, slightly to the right, and slide across to the centre.

  Swipe up / A / Up      approve: the card flies up into the "approved" counter
  Swipe down / D / Down  dismiss (U brings it back)
  Click / E / Enter      edit in place (Ctrl+Enter saves)
  S                      send everything approved; Tab shows the tasks you've sent

All behaviour lives in services/overlay_model.py; this file draws it, animates it and routes
input. While a text box has focus, letter keys go to the text box, never to actions.
"""

from __future__ import annotations

import html
import threading
from typing import Optional

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QTextDocument,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QWidget

from ..capture import win32
from ..services.overlay_model import OverlayModel
from .windows import logical_point_for_physical

# A light, airy card (it has to read well over any game or app).
INK = QColor("#151821")
MUTED = QColor("#6b7280")
FAINT = QColor("#9aa1ae")
CARD = QColor("#f8f9fc")
LINE = QColor(20, 24, 33, 22)
ACCENT = QColor("#4f7bff")
SUCCESS = QColor("#16a05a")
WARN = QColor("#d48806")
DANGER = QColor("#e5484d")
PILL_BG = QColor(255, 255, 255, 236)

STATE_COLOR = {"approved": MUTED, "sending": ACCENT, "sent": ACCENT, "working": ACCENT, "checking": ACCENT,
               "ready": SUCCESS, "needs_you": WARN, "merged": FAINT, "done": FAINT}
TYPE_LABEL = {"bug": "Bug", "improvement": "Improvement", "idea": "Idea", "task": "Task", "question": "Question", "note": "Note"}
ORIGIN = {"suggestion": "From what you said", "typed": "Your note", "quick": "Quick note", "ai": "Organised by local AI",
          "manual": "Added by you"}
KEYS = {Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter", Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Tab: "tab",
        Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right"}
TYPING = ("edit", "rename", "answer")

# Geometry (logical px). The window is only as big as the stack plus its pills.
CARD_W, CARD_H = 340, 476
W = 452
HEAD_H = 54
TOP = 14 + HEAD_H + 16  # card top
H = TOP + CARD_H + 18 + 40 + 40
CX = 24 + CARD_W / 2
CY = TOP + CARD_H / 2
IMG_FRAC = 0.54
SWIPE = 90  # px of drag that counts as a swipe
RADIUS = 24


def _font(px: float, weight: QFont.Weight = QFont.Weight.Normal, italic: bool = False) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(int(px))
    f.setWeight(weight)
    f.setItalic(italic)
    return f


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def _slot(i: float) -> tuple[float, float, float, float]:
    """(centre x, centre y, scale, opacity) of the i-th card in the stack (0 = front)."""
    i = max(0.0, i)
    opacity = 1 - i * 0.18 if i < 1 else max(0.0, 0.82 - (i - 1) * 0.34)
    return CX + 24 * i, CY - 6 * i, 1 - 0.065 * i, opacity


def _lerp_slot(a: float, b: float, t: float) -> tuple[float, float, float, float]:
    sa, sb = _slot(a), _slot(b)
    return tuple(x + (y - x) * t for x, y in zip(sa, sb))  # type: ignore[return-value]


def draw_lines(p: QPainter, text: str, font: QFont, color: QColor, x: float, y: float, w: float, max_lines: int) -> float:
    """Wrapped text, clamped to max_lines with an ellipsis. Returns the height used."""
    text = " ".join((text or "").split())
    if not text or max_lines <= 0:
        return 0.0
    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WordWrap)
    layout.setTextOption(opt)
    layout.beginLayout()
    lines = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(w)
        lines.append((line.textStart(), line.textLength()))
    layout.endLayout()
    fm = QFontMetricsF(font)
    lh = fm.lineSpacing()
    p.save()
    p.setFont(font)
    p.setPen(color)
    clipped = len(lines) > max_lines
    for i, (start, length) in enumerate(lines[:max_lines]):
        if clipped and i == max_lines - 1:
            part = fm.elidedText(text[start:], Qt.TextElideMode.ElideRight, w)
        else:
            part = text[start:start + length]
        p.drawText(QPointF(x, y + fm.ascent() + i * lh), part.rstrip())
    p.restore()
    return min(len(lines), max_lines) * lh


class _Art:
    """A card's screenshot, cropped to cover the card, and a tint taken from it for the glow."""

    def __init__(self, path: Optional[str]) -> None:
        self.cover: Optional[QPixmap] = None
        self.tint = QColor("#8fa8ff")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            return
        avg = img.scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation).pixelColor(0, 0)
        h, s, v, _ = avg.getHsvF()
        self.tint = QColor.fromHsvF(max(0.0, h), min(1.0, max(0.35, s * 1.4)), min(1.0, max(0.75, v * 1.3)))
        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        tw, th = int(CARD_W * dpr), int(CARD_H * IMG_FRAC * dpr)
        scaled = img.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        x, y = (scaled.width() - tw) // 2, (scaled.height() - th) // 2
        pm = QPixmap.fromImage(scaled.copy(x, y, tw, th))
        pm.setDevicePixelRatio(dpr)
        self.cover = pm


class _TypingFilter(QObject):
    """Ctrl+Enter / Enter / Esc inside the text boxes."""

    def __init__(self, overlay: "ReviewOverlay") -> None:
        super().__init__(overlay)
        self.o = overlay

    def eventFilter(self, obj, ev) -> bool:  # noqa: ANN001, N802
        if ev.type() != QEvent.Type.KeyPress:
            return False
        k, mods = ev.key(), ev.modifiers()
        if k == Qt.Key.Key_Escape:
            self.o.hide_overlay()
            return True
        enter = k in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if obj is self.o.editor and enter and mods & Qt.KeyboardModifier.ControlModifier:
            if self.o.model.mode == "answer":
                self.o.model.save_answer()
            else:
                self.o.model.save_edit()
            self.o.render()
            return True
        if obj is self.o.name_box and enter:
            self.o.model.save_rename()
            self.o.render()
            return True
        return False


class ReviewOverlay(QWidget):
    _async_done = Signal(object)

    def __init__(self, previews=None) -> None:  # noqa: ANN001
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self.model = OverlayModel(previews=previews)
        self.model.on_change = lambda: None
        self.model.run_async = self._run_async
        self._async_done.connect(lambda cb: cb())
        self.fg_hwnd = 0
        self._art: dict[str, _Art] = {}

        # One clock drives the card leaving (or coming back) and the stack sliding along.
        self.anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=300)
        self.anim.valueChanged.connect(lambda _v: self.update())
        self.anim.finished.connect(self._anim_done)
        self.anim_kind: Optional[str] = None  # out_up | out_down | in_up | in_down | snap | slide_next | slide_prev
        self.moving: Optional[dict] = None  # the card flying out
        self.from_dy = 0.0

        # Dragging the front card.
        self.drag_start: Optional[QPointF] = None
        self.drag = QPointF(0, 0)
        self.dragging = False

        self.editor = QPlainTextEdit(self)
        self.editor.setFrameStyle(0)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setAccessibleName("Card text: the first line is the title")
        self.editor.setStyleSheet("QPlainTextEdit { background: transparent; color: #151821; border: none; font-size: 15px;"
                                  " selection-background-color: #4f7bff; selection-color: white; }")
        self.editor.textChanged.connect(
            lambda: self.model.typed(self.editor.toPlainText()) if self.model.mode in ("edit", "answer") else None)
        self.editor.hide()
        self.name_box = QLineEdit(self)
        self.name_box.setAccessibleName("Session name")
        self.name_box.setStyleSheet("QLineEdit { background: white; color: #151821; border: 1px solid #4f7bff; border-radius: 10px;"
                                    " padding: 4px 8px; font-size: 14px; font-weight: 600; }")
        self.name_box.textChanged.connect(lambda t: self.model.typed(t) if self.model.mode == "rename" else None)
        self.name_box.hide()
        self.filter = _TypingFilter(self)
        self.editor.installEventFilter(self.filter)
        self.name_box.installEventFilter(self.filter)

        self.timer = QTimer(self, interval=1500, timeout=self._tick)
        self.msg_timer = QTimer(self, singleShot=True, interval=3500, timeout=self._clear_message)
        self._last_msg = ""

    # -------------------------------------------------------------- plumbing
    def _run_async(self, fn, done) -> None:  # noqa: ANN001
        """Slow work (restarting the preview) off the UI thread; `done` runs back on it."""
        def work() -> None:
            try:
                r, e = fn(), None
            except Exception as ex:  # noqa: BLE001
                r, e = None, ex
            self._async_done.emit(lambda: (done(r, e), self.render() if self.isVisible() else None))

        threading.Thread(target=work, daemon=True, name="overlay-work").start()

    def art(self, card: Optional[dict]) -> _Art:
        if card is None:
            return _Art(None)
        if card["id"] not in self._art:
            img = card.get("image") or {}
            self._art[card["id"]] = _Art(img.get("path"))
        return self._art[card["id"]]

    def stack(self) -> list[dict]:
        """Pending cards, current one first."""
        cards = self.model.cards
        ids = [c["id"] for c in cards]
        if self.model.current_id in ids:
            i = ids.index(self.model.current_id)
            cards = cards[i:] + cards[:i]
        return cards

    # -------------------------------------------------------------- show / hide
    def toggle(self, active_session_id: Optional[str], window: Optional[dict], work: Optional[dict], fg_hwnd: int) -> None:
        if self.isVisible():
            self.hide_overlay()
            return
        self.fg_hwnd = fg_hwnd
        try:
            self.model.open(active_session_id, window)
        except Exception as e:  # noqa: BLE001 - show the problem instead of failing silently
            self.model.error = f"Couldn't load the review: {e}"
        self._place(work)
        self.render()
        self.show()
        win32.exclude_from_capture(int(self.winId()))
        self.raise_()
        self.activateWindow()
        win32.restore_foreground(int(self.winId()))
        self._focus()
        self.timer.start()

    def hide_overlay(self) -> None:
        """Esc from anywhere: hide, keeping the current card, mode and any unsaved text."""
        self.timer.stop()
        self.anim.stop()
        self.anim_kind, self.moving = None, None
        self.drag, self.drag_start, self.dragging = QPointF(0, 0), None, False
        self.hide()
        if self.fg_hwnd:
            win32.restore_foreground(self.fg_hwnd)

    def _place(self, work: Optional[dict]) -> None:
        """Right-hand side of the screen you're working on, vertically centred: the rest stays visible."""
        if work:
            tl = logical_point_for_physical(work["left"], work["top"])
            br = logical_point_for_physical(work["left"] + work["width"] - 1, work["top"] + work["height"] - 1)
            area = QRect(tl, br)
        else:
            area = QGuiApplication.primaryScreen().availableGeometry()
        x = area.right() - W - 28
        y = area.top() + max(8, (area.height() - H) // 2)
        self.move(max(area.left(), x), y)

    def _tick(self) -> None:
        if self.model.mode in TYPING or self.anim.state() == QVariantAnimation.State.Running or self.dragging:
            return  # never redraw under someone typing or swiping
        try:
            self.model.refresh()
        except Exception:  # noqa: BLE001
            return
        self.render()

    def _focus(self) -> None:
        if self.model.mode in ("edit", "answer"):
            if not self.editor.hasFocus():
                self.editor.setFocus()
                c = self.editor.textCursor()
                c.movePosition(c.MoveOperation.End)
                self.editor.setTextCursor(c)
        elif self.model.mode == "rename":
            self.name_box.setFocus()
        else:
            self.setFocus()

    def _clear_message(self) -> None:
        self.model.message = ""
        self._last_msg = ""
        self.update()

    # -------------------------------------------------------------- actions with animation
    def act(self, key: str) -> bool:
        """Run a key through the model, animating card moves."""
        m = self.model
        if self.anim.state() == QVariantAnimation.State.Running:
            self.anim.setCurrentTime(self.anim.duration())  # finish the last move first
        front = m.card if m.mode == "review" else None
        undo_kind = m.history[-1].kind if (key == "u" and m.mode == "review" and m.history) else None
        before = m.current_id
        handled = m.key(key)
        if not handled:
            return False
        if front and key in ("a", "d") and not m.error:
            self._start("out_up" if key == "a" else "out_down", front, self.drag.y())
        elif undo_kind and not m.error:
            self._start("in_up" if undo_kind == "approve" else "in_down", None, 0.0)
        elif key in ("j", "k") and m.mode == "review" and m.current_id != before:
            self._start("slide_next" if key == "j" else "slide_prev", None, 0.0)
        self.drag = QPointF(0, 0)
        self.render()
        return True

    def _start(self, kind: str, card: Optional[dict], from_dy: float) -> None:
        self.anim.stop()
        self.anim_kind, self.moving, self.from_dy = kind, card, from_dy
        self.anim.setDuration(260 if kind.startswith("out") else 320)
        self.anim.start()

    def _anim_done(self) -> None:
        self.anim_kind, self.moving = None, None
        self.drag = QPointF(0, 0)
        self.update()

    # -------------------------------------------------------------- input
    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if ev.key() == Qt.Key.Key_Escape:
            self.hide_overlay()
            return
        if self.model.mode in TYPING:
            return super().keyPressEvent(ev)
        name = KEYS.get(ev.key()) or (ev.text() or "").lower()
        if self.model.mode == "review":
            name = {"up": "a", "down": "d", "enter": "e", "right": "j", "left": "k"}.get(name, name)
        if name and self.act(name):
            return
        super().keyPressEvent(ev)

    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802 - Tab switches views instead of moving focus
        if self.model.mode in TYPING:
            return super().focusNextPrevChild(_next)
        self.act("tab")
        return True

    @staticmethod
    def _front_rect() -> QRectF:
        return QRectF(CX - CARD_W / 2, CY - CARD_H / 2, CARD_W, CARD_H)

    @staticmethod
    def _badge_rect() -> QRectF:
        return QRectF(W - 24 - 138, 14 + 12, 128, 30)

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        pos = ev.position()
        if self._badge_rect().contains(pos) and self.model.approved_unsent:
            self.act("enter" if self.model.mode == "confirm" else "s")
            return
        if self.model.mode == "confirm" and self._front_rect().contains(pos):
            self.act("enter")
            return
        if self.model.mode == "review" and self.model.card and self._front_rect().contains(pos):
            self.drag_start = pos
            self.drag = QPointF(0, 0)
            self.dragging = False

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if self.drag_start is None:
            return
        d = ev.position() - self.drag_start
        if not self.dragging and abs(d.y()) + abs(d.x()) > 6:
            self.dragging = True
        if self.dragging:
            self.drag = QPointF(d.x() * 0.35, d.y())
            self.update()

    def mouseReleaseEvent(self, _ev: QMouseEvent) -> None:  # noqa: N802
        if self.drag_start is None:
            return
        self.drag_start = None
        if not self.dragging:
            self.act("e")  # a click edits
            return
        self.dragging = False
        dy = self.drag.y()
        if dy <= -SWIPE:
            self.act("a")
        elif dy >= SWIPE:
            self.act("d")
        else:
            self.drag = QPointF(0, 0)
            self._start("snap", None, dy)

    # -------------------------------------------------------------- render: the widgets that sit on the canvas
    def render(self) -> None:
        m = self.model
        mode = m.mode
        er = self._editor_rect()
        self.editor.setVisible(er is not None)
        if er is not None:
            self.editor.setGeometry(er.toRect())
            text = m.edit_text() if mode == "edit" else m.answer_text()
            if self.editor.toPlainText() != text:
                self.editor.blockSignals(True)
                self.editor.setPlainText(text)
                self.editor.blockSignals(False)
            self.editor.setPlaceholderText("Your answer…" if mode == "answer" else "First line is the title")
        self.name_box.setVisible(mode == "rename")
        if mode == "rename":
            self.name_box.setGeometry(QRect(38, 14 + 6, W - 24 - 138 - 52, 26))
            if self.name_box.text() != (m.name_edit or ""):
                self.name_box.setText(m.name_edit or "")
        msg = m.error or m.message
        if msg and msg != self._last_msg:
            self._last_msg = msg
            self.msg_timer.start(6000 if m.error else 3500)
        self.update()
        self._focus()

    def _editor_rect(self) -> Optional[QRectF]:
        mode = self.model.mode
        if mode == "edit" and self.model.card:
            top = CY - CARD_H / 2 + CARD_H * 0.30 + 24
            return QRectF(CX - CARD_W / 2 + 18, top, CARD_W - 36, CY + CARD_H / 2 - 46 - top)
        if mode == "answer" and self.model.task:
            top = CY - CARD_H / 2 + 200
            return QRectF(CX - CARD_W / 2 + 18, top, CARD_W - 36, CY + CARD_H / 2 - 46 - top)
        return None

    # -------------------------------------------------------------- painting
    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        m = self.model
        self._paint_header(p)
        kind = self.anim_kind
        t = _ease(float(self.anim.currentValue() or 0.0)) if kind else 1.0
        if m.mode in ("review", "edit", "rename") and m.card:
            cards = self.stack()
            shift = {"out_up": 1, "out_down": 1, "in_up": -1, "in_down": -1, "slide_next": 1, "slide_prev": -1}.get(kind or "", 0)
            for i in range(min(len(cards), 3) - 1, -1, -1):  # back to front
                c = cards[i]
                if kind in ("in_up", "in_down") and i == 0:
                    continue  # painted below, flying back in
                cx, cy, sc, op = _lerp_slot(i + shift, i, t) if shift else _slot(i)
                dy, rot = 0.0, 0.0
                if i == 0 and not kind:
                    dy, rot = self.drag.y(), self.drag.x() * 0.05
                    cx += self.drag.x()
                if i == 0 and kind == "snap":
                    dy = self.from_dy * (1 - t)
                self._paint_card(p, c, cx, cy + dy, sc, op, rot, front=(i == 0), editing=(i == 0 and m.mode == "edit"))
            if kind in ("in_up", "in_down"):
                start = -(CARD_H + 60) if kind == "in_up" else (CARD_H + 60)
                self._paint_card(p, cards[0], CX, CY + start * (1 - t), 1.0, t, 0.0, front=True)
        else:
            self._paint_sheet(p)
        if kind in ("out_up", "out_down") and self.moving:
            if kind == "out_up":  # approved: shrinks up into the "Send N approved" counter
                b = self._badge_rect().center()
                cx = CX + (b.x() - CX) * t
                cy = CY + self.from_dy + (b.y() - CY - self.from_dy) * t
                self._paint_card(p, self.moving, cx, cy, 1 - 0.9 * t, 1 - t ** 2, 0.0, front=True)
            else:  # dismissed: drops away and fades
                dy = self.from_dy + (CARD_H * 0.6 - self.from_dy) * t
                self._paint_card(p, self.moving, CX, CY + dy, 1 - 0.08 * t, 1 - t, 7 * t, front=True)
        self._paint_footer(p)
        p.end()

    @staticmethod
    def _shadow(p: QPainter, rect: QRectF, tint: QColor, strength: float) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(10, 0, -1):
            c = QColor(tint)
            c.setAlphaF(0.024 * strength)
            p.setBrush(c)
            p.drawRoundedRect(rect.adjusted(-k * 2.2, -k * 1.2 + 10, k * 2.2, k * 3 + 10), RADIUS + k * 2, RADIUS + k * 2)

    def _paint_card(self, p: QPainter, c: dict, cx: float, cy: float, scale: float, opacity: float, rot: float,
                    front: bool, editing: bool = False) -> None:
        art = self.art(c)
        p.save()
        p.setOpacity(max(0.0, min(1.0, opacity)))
        p.translate(cx, cy)
        p.rotate(rot)
        p.scale(scale, scale)
        r = QRectF(-CARD_W / 2, -CARD_H / 2, CARD_W, CARD_H)
        self._shadow(p, r, art.tint, 1.0 if front else 0.45)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS, RADIUS)
        bg = _mix(CARD, art.tint, 0.07)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawPath(path)
        p.setClipPath(path)
        full_h = CARD_H * IMG_FRAC
        img_h = CARD_H * 0.30 if editing else full_h
        if art.cover is not None:
            src_h = art.cover.height() * (img_h / full_h)
            src_y = (art.cover.height() - src_h) / 2
            p.drawPixmap(QRectF(r.left(), r.top(), CARD_W, img_h), art.cover, QRectF(0, src_y, art.cover.width(), src_h))
        else:  # no screenshot: a soft glow instead
            g = QRadialGradient(QPointF(0, r.top() + img_h * 0.4), CARD_W * 0.65)
            g.setColorAt(0, _mix(art.tint, QColor("white"), 0.1))
            g.setColorAt(1, bg)
            p.fillRect(QRectF(r.left(), r.top(), CARD_W, img_h), g)
        fade = QLinearGradient(0, r.top() + img_h * 0.4, 0, r.top() + img_h + 1)
        clear = QColor(bg)
        clear.setAlpha(0)
        fade.setColorAt(0, clear)
        fade.setColorAt(0.7, _mix(clear, bg, 0.8))
        fade.setColorAt(1, bg)
        p.fillRect(QRectF(r.left(), r.top() + img_h * 0.4, CARD_W, img_h * 0.6 + 2), fade)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 170), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        if front:
            self._paint_card_body(p, c, r, img_h, editing)
            self._paint_swipe_hint(p, r)
        p.restore()

    @staticmethod
    def _chip(p: QPainter, x: float, y: float, text: str, fg: QColor, bg: QColor) -> float:
        f = _font(11.5, QFont.Weight.DemiBold)
        w = QFontMetricsF(f).horizontalAdvance(text) + 18
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(x, y, w, 22), 11, 11)
        p.setFont(f)
        p.setPen(fg)
        p.drawText(QRectF(x, y, w, 22), Qt.AlignmentFlag.AlignCenter, text)
        return w

    def _paint_card_body(self, p: QPainter, c: dict, r: QRectF, img_h: float, editing: bool) -> None:
        x, w = r.left() + 22, CARD_W - 44
        y = r.top() + img_h - (12 if editing else 20)
        cw = self._chip(p, x, y, TYPE_LABEL.get(c["type"], c["type"]), INK, QColor(255, 255, 255, 235))
        origin = ORIGIN.get(c["origin"], "")
        if origin:
            p.setFont(_font(12))
            p.setPen(MUTED)
            p.drawText(QPointF(x + cw + 8, y + 15.5), origin)
        if editing:
            p.setFont(_font(12))
            p.setPen(MUTED)
            p.drawText(QPointF(x, r.bottom() - 20), "Ctrl+Enter saves  ·  the first line is the title")
            return
        y += 34
        y += draw_lines(p, c["title"], _font(20, QFont.Weight.Bold), INK, x, y, w, 3) + 6
        desc = (c.get("description") or "").strip()
        if desc and desc != c["title"].strip():
            y += draw_lines(p, desc, _font(13.5), MUTED, x, y, w, 3) + 6
        quotes = [q for q in c.get("quotes", []) if q.strip() and q.strip() not in desc]
        if quotes and y < r.bottom() - 110:
            draw_lines(p, f"“{quotes[0]}”", _font(13, italic=True), FAINT, x, y, w, 2)
        sim = c.get("similar_to")
        note = c.get("uncertainty") if c.get("needs_context") else None
        if sim or note:
            text = f"Looks like {sim['code']}, already sent. Approving adds this to it as evidence." if sim else note
            box = QRectF(x - 6, r.bottom() - 96, w + 12, 40)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(212, 136, 6, 28))
            p.drawRoundedRect(box, 10, 10)
            draw_lines(p, text, _font(12), WARN, box.left() + 10, box.top() + 4, box.width() - 20, 2)
        fy = r.bottom() - 44
        p.setPen(QPen(LINE, 1))
        p.drawLine(QPointF(r.left() + 18, fy - 4), QPointF(r.right() - 18, fy - 4))
        p.setFont(_font(12.5, QFont.Weight.DemiBold))
        cw = w / 3
        for i, (label, col) in enumerate((("↑  Approve", SUCCESS), ("↓  Dismiss", DANGER), ("Click to edit", MUTED))):
            p.setPen(col)
            p.drawText(QRectF(x + i * cw, fy + 4, cw, 26), Qt.AlignmentFlag.AlignCenter, label)

    def _paint_swipe_hint(self, p: QPainter, r: QRectF) -> None:
        dy = self.drag.y() if not self.anim_kind else 0
        if abs(dy) < 8:
            return
        up = dy < 0
        strength = min(1.0, abs(dy) / SWIPE)
        wash = QColor(SUCCESS if up else DANGER)
        wash.setAlphaF(0.12 * strength)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS, RADIUS)
        p.fillPath(path, wash)
        label = ("Release to approve" if up else "Release to dismiss") if strength >= 1 else ("Approve" if up else "Dismiss")
        f = _font(14, QFont.Weight.Bold)
        p.setFont(f)
        tw = QFontMetricsF(f).horizontalAdvance(label) + 30
        pill = QRectF(-tw / 2, (r.top() + 18) if up else (r.bottom() - 54), tw, 34)
        solid = QColor(SUCCESS if up else DANGER)
        solid.setAlphaF(0.92 * strength)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(solid)
        p.drawRoundedRect(pill, 17, 17)
        p.setPen(QColor(255, 255, 255, int(255 * strength)))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)

    # -------------------------------------------------------------- the sheet: tasks, send, choose, answer, all caught up
    def _sheet_html(self) -> tuple[str, str]:
        m = self.model
        st = m.state
        mode = m.mode
        e = html.escape
        muted = MUTED.name()
        if mode == "choose":
            rows = "".join(f"<p style='margin:8px 0'><b style='color:{ACCENT.name()}'>{i}</b>&nbsp;&nbsp;<b>{e(c['project'])}</b>"
                           f"<br><span style='color:{muted}'>{e(c['title'])}</span></p>" for i, c in enumerate(m.choices, 1))
            return "Which session?", rows
        if mode == "confirm" and m.plan:
            pl = m.plan
            items = "".join(f"<p style='margin:5px 0'>• {e(t['title'])}"
                            f"{f' <span style=color:{muted}>(follow-up to ' + t['follow_up_of'] + ')</span>' if t['follow_up_of'] else ''}</p>"
                            for t in pl["tasks"][:7])
            more = f"<p style='color:{muted}'>and {len(pl['tasks']) - 7} more</p>" if len(pl["tasks"]) > 7 else ""
            merges = "".join(f"<p style='margin:5px 0;color:{muted}'>• {e(x['title'])} → added to {x['into']}</p>" for x in pl["merges"])
            where = (f"<p style='color:{muted};margin-top:0'>to <b style='color:#151821'>{e(pl['agent_label'])}</b> on "
                     f"<span style='font-family:Consolas'>{e(pl['branch'] or '')}</span>{' (new)' if pl['branch_new'] else ''}</p>")
            wait = f"<p style='color:{muted}'>Starts when the current batch finishes.</p>" if pl["queued_behind_current"] else ""
            head = f"Send {pl['count']} task{'s' if pl['count'] != 1 else ''}" if pl["count"] else "Add repeat reports"
            return head, where + items + more + merges + wait
        if mode == "answer" and m.task:
            t = m.task
            return f"{t['code']} needs you", (f"<p style='margin-top:0'><b>{e(t['title'])}</b></p>"
                                             f"<p style='color:{WARN.name()}'>{e(t.get('note') or '')}</p>")
        if not st:
            return "Nothing to review", f"<p style='color:{muted}'>{e(m.message or 'Capture something with F8 or F9 first.')}</p>"
        rows = []
        for t in m.tasks[:8]:
            col = STATE_COLOR.get(t["state"], MUTED).name()
            picked = mode == "tasks" and t["id"] == m.task_id
            bg = "background-color:#eef2ff;" if picked else ""
            note = t.get("note") or ""
            if t["state"] == "ready" and not t.get("live") and (st.get("project") or {}).get("has_preview"):
                note = "Preview isn't serving this yet. " + note
            note_html = f"<br><span style='color:{muted}'>{e(note[:160])}</span>" if note and (picked or t["state"] == "needs_you") else ""
            rows.append(f"<table width='100%' cellpadding='7' style='{bg}'><tr><td><b>{t['code'] or '·'}</b>&nbsp; {e(t['title'][:70])}"
                        f"<br><span style='color:{col}'>● {e(t['label'] or '')}</span>{note_html}</td></tr></table>")
        pending = (st.get("counts") or {}).get("pending", 0)
        head = "Tasks" if mode == "tasks" or pending else "All caught up"
        empty = f"<p style='color:{muted}'>Nothing sent from this session yet.</p>" if not rows else ""
        return head, "".join(rows) + empty

    def _paint_sheet(self, p: QPainter) -> None:
        title, body = self._sheet_html()
        mode = self.model.mode
        r = QRectF(CX - CARD_W / 2, CY - CARD_H / 2, CARD_W, CARD_H)
        self._shadow(p, r, ACCENT if mode == "confirm" else QColor("#9aa7c7"), 0.8)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS, RADIUS)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(CARD)
        p.drawPath(path)
        if mode == "confirm":  # a glow that says "this is the send step"
            g = QRadialGradient(QPointF(r.center().x(), r.top()), CARD_W * 0.9)
            g.setColorAt(0, QColor(79, 123, 255, 64))
            g.setColorAt(1, QColor(79, 123, 255, 0))
            p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 170), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        x, w = r.left() + 24, CARD_W - 48
        p.setFont(_font(22, QFont.Weight.Bold))
        p.setPen(INK)
        p.drawText(QPointF(x, r.top() + 46), title)
        doc = QTextDocument()
        doc.setDefaultFont(_font(13.5))
        doc.setDocumentMargin(0)
        doc.setHtml(f"<div style='color:#151821'>{body}</div>")
        doc.setTextWidth(w)
        p.save()
        p.translate(x, r.top() + 64)
        avail = 120 if mode == "answer" else r.height() - 64 - (76 if mode == "confirm" else 24)
        doc.drawContents(p, QRectF(0, 0, w, avail))
        p.restore()
        if mode == "confirm":
            btn = QRectF(x, r.bottom() - 64, w, 44)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ACCENT)
            p.drawRoundedRect(btn, 22, 22)
            p.setFont(_font(14, QFont.Weight.Bold))
            p.setPen(QColor("white"))
            p.drawText(btn, Qt.AlignmentFlag.AlignCenter, "Send   ↵")
        if mode == "answer":
            p.setPen(QPen(LINE, 1))
            p.drawLine(QPointF(x, r.top() + 192), QPointF(r.right() - 24, r.top() + 192))
            p.setFont(_font(12))
            p.setPen(MUTED)
            p.drawText(QPointF(x, r.bottom() - 20), "Ctrl+Enter saves, then S sends it back")

    # -------------------------------------------------------------- header and footer pills
    @staticmethod
    def _pill(p: QPainter, r: QRectF) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(4, 0, -1):
            p.setBrush(QColor(20, 24, 33, 10))
            p.drawRoundedRect(r.adjusted(-k, -k + 3, k, k + 3), r.height() / 2 + k, r.height() / 2 + k)
        p.setBrush(PILL_BG)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

    def _paint_header(self, p: QPainter) -> None:
        m = self.model
        st = m.state
        sess = st.get("session") or {}
        r = QRectF(24, 14, W - 48, HEAD_H)
        self._pill(p, r)
        if m.mode != "rename":
            f = _font(14, QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(INK)
            name = ("🔒 " if sess.get("name_locked") else "") + (sess.get("title") or "Review")
            p.drawText(QPointF(r.left() + 20, r.top() + 23), QFontMetricsF(f).elidedText(name, Qt.TextElideMode.ElideRight, r.width() - 180))
        parts = []
        counts = st.get("counts", {})
        for key, label in (("working", "working"), ("checking", "checking"), ("ready", "ready"), ("needs_you", "need you")):
            if counts.get(key):
                parts.append((f"{counts[key]} {label}", STATE_COLOR[key]))
        if st.get("incoming"):
            parts.append((f"{st['incoming']} on the way", FAINT))
        runs = [x for x in st.get("runs", []) if x["state"] in ("starting", "running", "checking")]
        if runs and runs[0].get("stage"):
            parts.append((runs[0]["stage"][:40], ACCENT))
        if st.get("ready_to_refresh"):
            parts.insert(0, ("Ready to refresh", SUCCESS))
        if not parts:
            parts = [(f"{len(m.cards)} to review" if m.cards else "Nothing waiting", MUTED)]
        f = _font(11.5)
        p.setFont(f)
        fm = QFontMetricsF(f)
        x, limit = r.left() + 20, r.right() - 160
        for text, col in parts:
            if x + fm.horizontalAdvance(text) > limit:
                break
            p.setPen(col)
            p.drawText(QPointF(x, r.top() + 42), text)
            x += fm.horizontalAdvance(text) + 12
        # The approved counter: where approved cards fly to. Click it (or press S) to send.
        n = m.approved_unsent
        b = self._badge_rect()
        pulse = self.anim_kind == "out_up" and float(self.anim.currentValue() or 0) > 0.55
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ACCENT if n else QColor(20, 24, 33, 14))
        p.drawRoundedRect(b.adjusted(-3, -3, 3, 3) if pulse else b, 15, 15)
        p.setFont(_font(12.5, QFont.Weight.Bold))
        p.setPen(QColor("white") if n else MUTED)
        p.drawText(b, Qt.AlignmentFlag.AlignCenter, f"Send {n} approved" if n else "0 approved")

    def _paint_footer(self, p: QPainter) -> None:
        m = self.model
        y = TOP + CARD_H + 18
        msg = m.error or m.message
        if msg:
            f = _font(12.5, QFont.Weight.DemiBold)
            fm = QFontMetricsF(f)
            text = fm.elidedText(msg, Qt.TextElideMode.ElideRight, W - 80)
            w = fm.horizontalAdvance(text) + 32
            r = QRectF(max(10.0, CX + 12 - w / 2), y, w, 30)
            self._pill(p, r)
            p.setFont(f)
            p.setPen(DANGER if m.error else INK)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        keys = self._keys()
        f, bold = _font(11.5), _font(11.5, QFont.Weight.Bold)
        fm, fb = QFontMetricsF(f), QFontMetricsF(bold)
        total = sum(fb.horizontalAdvance(k) + 4 + fm.horizontalAdvance(lbl) + 14 for k, lbl in keys) + 12
        r = QRectF(max(10.0, CX + 12 - total / 2), y + 40, min(total, W - 20), 28)
        self._pill(p, r)
        x = r.left() + 12
        for k, lbl in keys:
            p.setFont(bold)
            p.setPen(INK)
            p.drawText(QPointF(x, r.top() + 18.5), k)
            x += fb.horizontalAdvance(k) + 4
            p.setFont(f)
            p.setPen(MUTED)
            p.drawText(QPointF(x, r.top() + 18.5), lbl)
            x += fm.horizontalAdvance(lbl) + 14

    def _keys(self) -> list[tuple[str, str]]:
        mode = self.model.mode
        if mode in ("edit", "answer"):
            return [("Ctrl+Enter", "save"), ("Esc", "hide, keeps your text")]
        if mode == "rename":
            return [("Enter", "save name"), ("Esc", "hide")]
        if mode == "confirm":
            return [("Enter", "send"), ("Backspace", "back"), ("Esc", "hide")]
        if mode == "choose":
            return [("1-9", "pick"), ("Esc", "hide")]
        if mode == "tasks":
            t = self.model.task
            keys = [("J/K", "pick")]
            if t and t["state"] in ("ready", "needs_you"):
                keys += [("Y", "works"), ("F", "still broken")]
            if t and t["state"] == "needs_you":
                keys += [("E", "answer")]
            return keys + [("R", "preview"), ("Tab", "cards"), ("Esc", "hide")]
        return [("U", "undo"), ("S", "send"), ("N", "rename"), ("R", "preview"), ("Tab", "tasks"), ("Esc", "hide")]
