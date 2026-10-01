"""The project switcher (Shift+F9): a floating card for jumping between projects or starting a new one.

  Type                 filter your projects (most recently used first)
  Up / Down / hover    pick one; Enter or a click starts a session there, ending the running one
  Ctrl+N / Tab         new project, prefilled from the last active project; Enter creates it and starts a session
  Alt+Left             back to the list (the new project's fields are kept)
  Esc                  hide

Looks like the review overlay (Ctrl+F9) and sits in the same place. All behaviour lives in
services/switcher_model.py; this file draws it and routes input.
"""

from __future__ import annotations

import threading
import zlib
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from ..capture import win32
from ..services.switcher_model import SwitcherModel
from .overlay import ACCENT, CARD, DANGER, FAINT, INK, LINE, MUTED, RADIUS, SUCCESS, _font, paint_keys, paint_message, paint_pill, paint_shadow
from .windows import logical_point_for_physical

CARD_W, CARD_H = 380, 520
W = CARD_W + 48
HEAD_H = 54
TOP = 14 + HEAD_H + 16
H = TOP + CARD_H + 18 + 40 + 40
LEFT = 24
ROWS_TOP = TOP + 78
ROW_H = 56
VISIBLE = (CARD_H - (ROWS_TOP - TOP) - 12) // ROW_H

FORM_CSS = """
QWidget#form { background: transparent; }
QLabel { color: #6b7280; font-family: 'Segoe UI'; font-size: 11px; font-weight: 600; }
QLabel#status { font-weight: 400; }
QLineEdit { background: white; color: #151821; border: 1px solid rgba(20,24,33,0.13); border-radius: 9px; padding: 4px 9px;
            font-family: 'Segoe UI'; font-size: 13px; selection-background-color: #4f7bff; selection-color: white; }
QLineEdit:focus { border: 1px solid #4f7bff; }
QPushButton { background: rgba(20,24,33,0.06); color: #151821; border: 1px solid transparent; border-radius: 13px;
              padding: 4px 11px; font-family: 'Segoe UI'; font-size: 12px; font-weight: 600; }
QPushButton:hover { background: rgba(20,24,33,0.10); }
QPushButton:checked { background: #4f7bff; color: white; }
QPushButton:focus { border: 1px solid #4f7bff; }
"""
GO_CSS = ("QPushButton { background: #4f7bff; color: white; border: none; border-radius: 22px; font-family: 'Segoe UI';"
          " font-size: 14px; font-weight: 700; } QPushButton:hover { background: #3f6bf0; }"
          " QPushButton:focus { border: 2px solid #c9d6ff; }")
SEARCH_CSS = ("QLineEdit { background: rgba(20,24,33,0.05); color: #151821; border: 1px solid transparent; border-radius: 14px;"
              " padding: 8px 14px; font-family: 'Segoe UI'; font-size: 16px; selection-background-color: #4f7bff;"
              " selection-color: white; } QLineEdit:focus { border: 1px solid rgba(79,123,255,0.45); }")

TEXT_FIELDS = (("name", "Name", "What you're testing"), ("repo_path", "Repo folder", "C:\\path\\to\\repo (optional)"),
               ("base_branch", "Base branch", "main"), ("program", "Program", "Game.exe or window title"),
               ("setup_command", "Setup", "e.g. npm install"), ("check_command", "Check", "e.g. npm test"),
               ("preview_command", "Preview", "e.g. npm run dev"), ("preview_url", "Preview URL", "http://localhost:5173"))


def _avatar_color(name: str) -> QColor:
    return QColor.fromHsv(zlib.crc32(name.lower().encode()) % 360, 120, 215)


class _Keys(QObject):
    """Keys inside the search box and the form: they drive the switcher instead of the text box where it matters."""

    def __init__(self, sw: "ProjectSwitcher") -> None:
        super().__init__(sw)
        self.sw = sw

    def eventFilter(self, obj, ev) -> bool:  # noqa: ANN001, N802
        if ev.type() != QEvent.Type.KeyPress:
            return False
        return self.sw.handle_key(ev)


class ProjectSwitcher(QWidget):
    _async_done = Signal(object)

    def __init__(self, sessions=None, on_done: Optional[Callable[[str, Optional[dict]], None]] = None) -> None:  # noqa: ANN001
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self.setMouseTracking(True)
        self.model = SwitcherModel(sessions=sessions)
        self.model.run_async = self._run_async
        self._async_done.connect(lambda cb: cb())
        self.on_done = on_done or (lambda msg, work: None)
        self.fg_hwnd = 0
        self.work: Optional[dict] = None
        self.scroll = 0
        self._shown_mode = ""
        self.keys = _Keys(self)

        self.search = QLineEdit(self)
        self.search.setAccessibleName("Search projects, or type a new project's name")
        self.search.setPlaceholderText("Switch to… or name a new project")
        self.search.setStyleSheet(SEARCH_CSS)
        self.search.setGeometry(LEFT + 18, TOP + 18, CARD_W - 36, 44)
        self.search.textChanged.connect(self._searched)
        self.search.installEventFilter(self.keys)

        self.form = QWidget(self, objectName="form")
        self.form.setStyleSheet(FORM_CSS)
        self.form.setGeometry(LEFT + 20, TOP + 80, CARD_W - 40, CARD_H - 80 - 74)
        grid = QGridLayout(self.form)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        self.boxes: dict[str, QLineEdit] = {}
        for key, label, hint in TEXT_FIELDS:
            box = QLineEdit(self.form)
            box.setAccessibleName(label)
            box.setPlaceholderText(hint)
            box.setFixedHeight(30)
            box.textEdited.connect(lambda t, k=key: self._edited(k, t))
            box.installEventFilter(self.keys)
            self.boxes[key] = box
        self.status = QLabel(self.form, objectName="status")
        self.agent = self._choices([("none", "None"), ("claude", "Claude Code"), ("codex", "Codex")], "default_agent")
        self.access = self._choices([("standard", "Standard"), ("full", "Full access")], "agent_access")
        self.mic = self._toggle("Mic", "mic")
        self.loopback = self._toggle("PC audio", "loopback")

        def cell(label: str, w: QWidget) -> QWidget:
            box = QWidget(self.form)
            lay = QGridLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setVerticalSpacing(3)
            lay.addWidget(QLabel(label.upper(), box), 0, 0)
            lay.addWidget(w, 1, 0)
            return box

        def row(*buttons: QPushButton) -> QWidget:
            box = QWidget(self.form)
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(5)
            for b in buttons:
                lay.addWidget(b)
            lay.addStretch(1)
            return box

        b = self.boxes
        grid.addWidget(cell("Name", b["name"]), 0, 0, 1, 2)
        grid.addWidget(cell("Repo folder", b["repo_path"]), 1, 0, 1, 2)
        grid.addWidget(self.status, 2, 0, 1, 2)
        grid.addWidget(cell("Base branch", b["base_branch"]), 3, 0)
        grid.addWidget(cell("Program", b["program"]), 3, 1)
        grid.addWidget(cell("Agent", row(*self.agent.buttons())), 4, 0, 1, 2)
        grid.addWidget(cell("Access", row(*self.access.buttons())), 5, 0)
        grid.addWidget(cell("Record", row(self.mic, self.loopback)), 5, 1)
        grid.addWidget(cell("Setup", b["setup_command"]), 6, 0)
        grid.addWidget(cell("Check", b["check_command"]), 6, 1)
        grid.addWidget(cell("Preview", b["preview_command"]), 7, 0)
        grid.addWidget(cell("Preview URL", b["preview_url"]), 7, 1)
        grid.setRowStretch(8, 1)
        order = [b["name"], b["repo_path"], b["base_branch"], b["program"], *self.agent.buttons(), *self.access.buttons(),
                 self.mic, self.loopback, b["setup_command"], b["check_command"], b["preview_command"], b["preview_url"]]

        self.go = QPushButton("Create and start session   ↵", self)
        self.go.setStyleSheet(GO_CSS)
        self.go.setGeometry(LEFT + 22, TOP + CARD_H - 62, CARD_W - 44, 44)
        self.go.clicked.connect(self._create)
        self.go.installEventFilter(self.keys)
        for a, z in zip([*order, self.go], [*order[1:], self.go, order[0]]):
            QWidget.setTabOrder(a, z)

        self.msg_timer = QTimer(self, singleShot=True, timeout=self._clear_message)
        self._last_msg = ""

    # -------------------------------------------------------------- building blocks
    def _choices(self, options: list[tuple[str, str]], key: str) -> QButtonGroup:
        group = QButtonGroup(self)
        group.setExclusive(True)
        for value, label in options:
            btn = QPushButton(label, self.form, checkable=True)
            btn.setProperty("value", value)
            btn.setFixedHeight(28)
            btn.installEventFilter(self.keys)
            group.addButton(btn)
        group.buttonClicked.connect(lambda btn: self._edited(key, btn.property("value")))
        return group

    def _toggle(self, label: str, key: str) -> QPushButton:
        btn = QPushButton(label, self.form, checkable=True)
        btn.setFixedHeight(28)
        btn.installEventFilter(self.keys)
        btn.toggled.connect(lambda on: self._edited(key, on))
        return btn

    def _run_async(self, fn, done) -> None:  # noqa: ANN001
        """Checking the repo folder runs git: off the UI thread; `done` runs back on it."""
        def work() -> None:
            try:
                r, e = fn(), None
            except Exception as ex:  # noqa: BLE001
                r, e = None, ex
            self._async_done.emit(lambda: (done(r, e), self.render() if self.isVisible() else None))

        threading.Thread(target=work, daemon=True, name="switcher-work").start()

    # -------------------------------------------------------------- show / hide
    def toggle(self, window: Optional[dict], work: Optional[dict], fg_hwnd: int) -> None:
        if self.isVisible():
            self.hide_switcher()
            return
        self.fg_hwnd, self.work = fg_hwnd, work
        try:
            self.model.open(window)
        except Exception as e:  # noqa: BLE001 - show the problem instead of failing silently
            self.model.error = f"Couldn't load your projects: {e}"
        self.scroll = 0
        self._shown_mode = ""
        if self.model.mode == "list":
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self._place(work)
        self.render()
        self.show()
        win32.exclude_from_capture(int(self.winId()))
        self.raise_()
        self.activateWindow()
        win32.restore_foreground(int(self.winId()))
        self._focus(force=True)

    def hide_switcher(self, restore: bool = True) -> None:
        self.hide()
        if restore and self.fg_hwnd:
            win32.restore_foreground(self.fg_hwnd)

    def _place(self, work: Optional[dict]) -> None:
        """Right-hand side of the screen you're working on, like the review overlay."""
        if work:
            tl = logical_point_for_physical(work["left"], work["top"])
            br = logical_point_for_physical(work["left"] + work["width"] - 1, work["top"] + work["height"] - 1)
            area = QRect(tl, br)
        else:
            area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(max(area.left(), area.right() - W - 28), area.top() + max(8, (area.height() - H) // 2))

    def _clear_message(self) -> None:
        self.model.error = self.model.message = ""
        self._last_msg = ""
        self.update()

    # -------------------------------------------------------------- actions
    def _finish(self, msg: Optional[str]) -> None:
        if msg is None:
            self.render()
            return
        self.hide_switcher()
        self.on_done(msg, self.work)

    def _searched(self, text: str) -> None:
        self.model.set_query(text)
        self.scroll = 0
        self.render()

    def _edited(self, key: str, value) -> None:  # noqa: ANN001
        self.model.set_field(key, value)
        self.render()

    def _create(self) -> None:
        self._finish(self.model.create())

    def _new(self) -> None:
        self.model.start_new(self.search.text())
        self.render()

    def _back(self) -> None:
        self.model.back()
        self.search.blockSignals(True)
        self.search.setText(self.model.query)
        self.search.blockSignals(False)
        self.render()

    def handle_key(self, ev: QKeyEvent) -> bool:
        k, mods = ev.key(), ev.modifiers()
        m = self.model
        if k == Qt.Key.Key_Escape:
            self.hide_switcher()
            return True
        enter = k in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if m.mode == "list":
            if k in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                m.move({Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1, Qt.Key.Key_PageUp: -5, Qt.Key.Key_PageDown: 5}[k])
                self.render()
                return True
            if enter:
                self._finish(m.choose())
                return True
            if k == Qt.Key.Key_Tab or (k == Qt.Key.Key_N and mods & Qt.KeyboardModifier.ControlModifier):
                self._new()
                return True
            return False
        if k == Qt.Key.Key_Left and mods & Qt.KeyboardModifier.AltModifier:
            self._back()
            return True
        if enter and not mods & Qt.KeyboardModifier.ShiftModifier:
            self._create()
            return True
        return False

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if not self.handle_key(ev):
            super().keyPressEvent(ev)

    # -------------------------------------------------------------- mouse
    @staticmethod
    def _badge_rect() -> QRectF:
        return QRectF(W - 24 - 138, 14 + 12, 128, 30)

    def _row_at(self, pos: QPointF) -> Optional[int]:
        if self.model.mode != "list" or not (LEFT <= pos.x() <= LEFT + CARD_W) or pos.y() < ROWS_TOP:
            return None
        i = int((pos.y() - ROWS_TOP) // ROW_H)
        if i >= VISIBLE:
            return None
        i += self.scroll
        return i if i < self.model.count else None

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        pos = ev.position()
        if self._badge_rect().contains(pos):
            self._back() if self.model.mode == "new" else self._new()
            return
        i = self._row_at(pos)
        if i is not None:
            self._finish(self.model.choose(i))

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        i = self._row_at(ev.position())
        if i is not None and i != self.model.index:
            self.model.index = i
            self.update()

    def wheelEvent(self, ev) -> None:  # noqa: ANN001, N802
        if self.model.mode == "list":
            self.model.move(-1 if ev.angleDelta().y() > 0 else 1)
            self.render()

    # -------------------------------------------------------------- render: the widgets on the card
    def render(self) -> None:
        m = self.model
        listing = m.mode == "list"
        self.search.setVisible(listing)
        self.form.setVisible(not listing)
        self.go.setVisible(not listing)
        if listing:
            if m.index < self.scroll:
                self.scroll = m.index
            elif m.index >= self.scroll + VISIBLE:
                self.scroll = m.index - VISIBLE + 1
        else:
            d = m.draft
            for key, box in self.boxes.items():
                text = d.get(key) or ""
                if box.text() != text:
                    box.setText(text)
            st = m.repo_status
            self.boxes["base_branch"].setPlaceholderText(st.get("branch") or "main")
            self.status.setText(st.get("text", ""))
            col = SUCCESS if st.get("ok") else (DANGER if st.get("ok") is False else MUTED)
            self.status.setStyleSheet(f"color: {col.name()};")
            for group, key in ((self.agent, "default_agent"), (self.access, "agent_access")):
                for btn in group.buttons():
                    if btn.property("value") == d.get(key) and not btn.isChecked():
                        btn.setChecked(True)
            for btn in self.access.buttons():
                btn.setEnabled(d.get("default_agent") != "none")
            for btn, key in ((self.mic, "mic"), (self.loopback, "loopback")):
                if btn.isChecked() != bool(d.get(key)):
                    btn.blockSignals(True)
                    btn.setChecked(bool(d.get(key)))
                    btn.blockSignals(False)
        msg = m.error or m.message
        if msg and msg != self._last_msg:
            self._last_msg = msg
            self.msg_timer.start(6000 if m.error else 3500)
        self.update()
        self._focus()

    def _focus(self, force: bool = False) -> None:
        """Only when the mode changes, so a re-render never pulls focus out of the box you're typing in."""
        mode = self.model.mode
        if mode == self._shown_mode and not force:
            return
        self._shown_mode = mode
        if mode == "list":
            self.search.setFocus()
        else:
            box = self.boxes["name"]
            box.setFocus()
            box.selectAll() if not self.model.draft.get("name") else box.end(False)

    # -------------------------------------------------------------- painting
    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        m = self.model
        self._paint_header(p)
        r = QRectF(LEFT, TOP, CARD_W, CARD_H)
        paint_shadow(p, r, QColor("#9aa7c7") if m.mode == "list" else ACCENT, 0.8)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS, RADIUS)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(CARD)
        p.drawPath(path)
        if m.mode == "new":  # the same "about to do something" glow as the send step
            g = QRadialGradient(QPointF(r.center().x(), r.top()), CARD_W * 0.9)
            g.setColorAt(0, QColor(79, 123, 255, 56))
            g.setColorAt(1, QColor(79, 123, 255, 0))
            p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 170), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        if m.mode == "list":
            self._paint_list(p, r)
        else:
            self._paint_new_head(p, r)
        y = TOP + CARD_H + 18
        msg = m.error or m.message
        if msg:
            paint_message(p, msg, bool(m.error), LEFT + CARD_W / 2, y, W - 60)
        paint_keys(p, self._keys(), LEFT + CARD_W / 2, y + 40, W - 20)
        p.end()

    def _paint_header(self, p: QPainter) -> None:
        m = self.model
        r = QRectF(24, 14, W - 48, HEAD_H)
        paint_pill(p, r)
        p.setFont(_font(14, QFont.Weight.Bold))
        p.setPen(INK)
        p.drawText(QPointF(r.left() + 20, r.top() + 23), "Switch project")
        f = _font(11.5)
        p.setFont(f)
        if m.running_name:
            p.setPen(SUCCESS)
            text = f"●  Recording in {m.running_name}"
        else:
            p.setPen(MUTED)
            text = "No session running"
        p.drawText(QPointF(r.left() + 20, r.top() + 42), QFontMetricsF(f).elidedText(text, Qt.TextElideMode.ElideRight, r.width() - 180))
        b = self._badge_rect()
        new = m.mode == "new"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 24, 33, 14) if new else ACCENT)
        p.drawRoundedRect(b, 15, 15)
        p.setFont(_font(12.5, QFont.Weight.Bold))
        p.setPen(INK if new else QColor("white"))
        p.drawText(b, Qt.AlignmentFlag.AlignCenter, "‹  Projects" if new else "+  New project")

    def _paint_list(self, p: QPainter, card: QRectF) -> None:
        m = self.model
        rows = m.filtered
        p.setPen(QPen(LINE, 1))
        p.drawLine(QPointF(card.left() + 18, ROWS_TOP - 8), QPointF(card.right() - 18, ROWS_TOP - 8))
        end = min(m.count, self.scroll + VISIBLE)
        for i in range(self.scroll, end):
            y = ROWS_TOP + (i - self.scroll) * ROW_H
            rr = QRectF(card.left() + 10, y, CARD_W - 20, ROW_H - 4)
            picked = i == m.index
            if picked:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#eef2ff"))
                p.drawRoundedRect(rr, 14, 14)
            if i < len(rows):
                self._paint_row(p, rr, rows[i], picked)
            else:
                self._paint_new_row(p, rr, picked)
        if m.count > end:  # more below
            p.setFont(_font(11.5))
            p.setPen(FAINT)
            p.drawText(QRectF(card.left(), card.bottom() - 18, CARD_W, 14), Qt.AlignmentFlag.AlignCenter, f"{m.count - end} more ↓")

    @staticmethod
    def _avatar(p: QPainter, rr: QRectF, color: QColor, letter: str) -> None:
        a = QRectF(rr.left() + 10, rr.center().y() - 18, 36, 36)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(a, 11, 11)
        p.setFont(_font(16, QFont.Weight.Bold))
        p.setPen(QColor("white"))
        p.drawText(a, Qt.AlignmentFlag.AlignCenter, letter)

    @staticmethod
    def _badge(p: QPainter, right: float, cy: float, text: str, color: QColor) -> float:
        f = _font(11, QFont.Weight.Bold)
        w = QFontMetricsF(f).horizontalAdvance(text) + 18
        r = QRectF(right - w, cy - 11, w, 22)
        bg = QColor(color)
        bg.setAlpha(30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 11, 11)
        p.setFont(f)
        p.setPen(color)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        return w

    def _paint_row(self, p: QPainter, rr: QRectF, row: dict, picked: bool) -> None:
        self._avatar(p, rr, _avatar_color(row["name"]), (row["name"].strip()[:1] or "?").upper())
        right = rr.right() - 12
        if row["running"]:
            right -= self._badge(p, right, rr.center().y(), "Recording", SUCCESS) + 8
        elif row["detected"]:
            right -= self._badge(p, right, rr.center().y(), "Open now", ACCENT) + 8
        elif picked:
            p.setFont(_font(12, QFont.Weight.DemiBold))
            p.setPen(ACCENT)
            p.drawText(QRectF(right - 60, rr.top(), 60, rr.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "Start  ↵")
            right -= 68
        x = rr.left() + 58
        f = _font(15, QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(INK)
        p.drawText(QPointF(x, rr.top() + 22), QFontMetricsF(f).elidedText(row["name"], Qt.TextElideMode.ElideRight, right - x))
        parts = [row["ago"]]
        if row["pending"]:
            parts.append(f"{row['pending']} to review")
        if row["agent_label"]:
            parts.append(row["agent_label"])
        if row["repo"]:
            parts.append(row["repo"])
        f = _font(12)
        p.setFont(f)
        p.setPen(MUTED)
        p.drawText(QPointF(x, rr.top() + 40), QFontMetricsF(f).elidedText("  ·  ".join(parts), Qt.TextElideMode.ElideRight, right - x))

    def _paint_new_row(self, p: QPainter, rr: QRectF, picked: bool) -> None:
        m = self.model
        self._avatar(p, rr, ACCENT if picked else QColor("#b8c6f5"), "+")
        x = rr.left() + 58
        q = m.query.strip()
        title = f"New project “{q}”" if q and not m.exact else "New project"
        f = _font(15, QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(ACCENT if picked else INK)
        p.drawText(QPointF(x, rr.top() + 22), QFontMetricsF(f).elidedText(title, Qt.TextElideMode.ElideRight, rr.right() - x - 12))
        t = m._template()
        sub = f"Same settings as {t['name']}, change anything" if t else "Set it up and start a session"
        f = _font(12)
        p.setFont(f)
        p.setPen(MUTED)
        p.drawText(QPointF(x, rr.top() + 40), QFontMetricsF(f).elidedText(sub, Qt.TextElideMode.ElideRight, rr.right() - x - 12))

    def _paint_new_head(self, p: QPainter, card: QRectF) -> None:
        m = self.model
        x = card.left() + 22
        p.setFont(_font(22, QFont.Weight.Bold))
        p.setPen(INK)
        p.drawText(QPointF(x, card.top() + 44), "New project")
        t = m.template
        sub = f"Settings copied from {t['name']} — change anything" if t else "Set it up, then start a session"
        f = _font(12.5)
        p.setFont(f)
        p.setPen(MUTED)
        p.drawText(QPointF(x, card.top() + 64), QFontMetricsF(f).elidedText(sub, Qt.TextElideMode.ElideRight, CARD_W - 44))

    def _keys(self) -> list[tuple[str, str]]:
        m = self.model
        if m.mode == "new":
            return [("Tab", "next"), ("Enter", "create & start"), ("Alt+←", "projects"), ("Esc", "hide")]
        if m.on_new_row:
            go = "new project"
        elif m.selected and m.selected["running"]:
            go = "recording here"
        else:
            go = "end & switch" if m.running_pid else "start session"
        return [("↑↓", "pick"), ("Enter", go), ("Ctrl+N", "new"), ("Esc", "hide")]
