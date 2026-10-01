"""The keyboard-first review overlay (Ctrl+F9): one card at a time, over whatever you're testing.

All behaviour lives in services/overlay_model.py; this file only draws it and routes keys.
While a text box has focus, letter keys go to the text box, never to actions.
"""

from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLayout, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..capture import win32
from ..services.overlay_model import OverlayModel
from .windows import ACCENT, BG, BORDER, MUTED, STYLE, SUCCESS, TEXT, WARN, logical_point_for_physical

PILL = {"approved": MUTED, "sending": ACCENT, "sent": ACCENT, "working": ACCENT, "checking": ACCENT,
        "ready": SUCCESS, "needs_you": WARN, "merged": MUTED}
TYPE_LABEL = {"bug": "Bug", "improvement": "Improvement", "idea": "Idea", "task": "Task", "question": "Question", "note": "Note"}
KEYS = {Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter", Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Tab: "tab",
        Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down"}
TYPING = ("edit", "rename", "answer")

EXTRA = f"""
QLabel#h1 {{ font-size: 17px; font-weight: 600; }}
QLabel#cardtitle {{ font-size: 18px; font-weight: 600; }}
QLabel#tag {{ color: {MUTED}; font-size: 12px; border: 1px solid {BORDER}; border-radius: 9px; padding: 1px 8px; }}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#msg {{ color: {SUCCESS}; font-size: 13px; }}
QLabel#similar {{ color: {WARN}; font-size: 13px; }}
QLabel#quote {{ color: {MUTED}; font-size: 13px; font-style: italic; }}
QLineEdit {{ background: {BG}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px; font-size: 15px; }}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
"""


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
        self.model = OverlayModel(previews=previews)
        self.fg_hwnd = 0
        self.setStyleSheet(STYLE + EXTRA)
        root = QWidget(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)  # grow with wrapped text; never squash the card
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        self.name = QLabel()
        self.name.setObjectName("h1")
        self.name_box = QLineEdit()
        self.name_box.setAccessibleName("Session name")
        self.progress = QLabel()
        self.progress.setObjectName("hint")
        head.addWidget(self.name, 1)
        head.addWidget(self.name_box, 1)
        head.addWidget(self.progress)
        lay.addLayout(head)
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)

        # card
        self.card_box = QWidget()
        cl = QVBoxLayout(self.card_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        tags = QHBoxLayout()
        self.type_tag = QLabel()
        self.type_tag.setObjectName("tag")
        self.origin_tag = QLabel()
        self.origin_tag.setObjectName("hint")
        tags.addWidget(self.type_tag)
        tags.addWidget(self.origin_tag, 1)
        cl.addLayout(tags)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setStyleSheet(f"background:{BG}; border:1px solid {BORDER}; border-radius:8px;")
        self.image.setFixedHeight(300)
        cl.addWidget(self.image)
        self.title = QLabel()
        self.title.setObjectName("cardtitle")
        self.title.setWordWrap(True)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.quotes = QLabel()
        self.quotes.setObjectName("quote")
        self.quotes.setWordWrap(True)
        self.similar = QLabel()
        self.similar.setObjectName("similar")
        self.similar.setWordWrap(True)
        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName("Card text: the first line is the title")
        self.editor.setMinimumHeight(150)
        self.editor.textChanged.connect(lambda: self.model.typed(self.editor.toPlainText()) if self.model.mode in ("edit", "answer") else None)
        self.name_box.textChanged.connect(lambda t: self.model.typed(t) if self.model.mode == "rename" else None)
        for w in (self.title, self.desc, self.quotes, self.similar, self.editor):
            cl.addWidget(w)
        lay.addWidget(self.card_box)

        # other views (tasks, confirm, choose, empty)
        self.panel = QLabel()
        self.panel.setWordWrap(True)
        self.panel.setTextFormat(Qt.TextFormat.RichText)
        self.panel.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.panel.setMinimumHeight(120)
        lay.addWidget(self.panel, 1)

        self.msg = QLabel()
        self.msg.setObjectName("msg")
        self.msg.setWordWrap(True)
        self.err = QLabel()
        self.err.setObjectName("error")
        self.err.setWordWrap(True)
        lay.addWidget(self.msg)
        lay.addWidget(self.err)

        foot = QHBoxLayout()
        self.keys = QLabel()
        self.keys.setObjectName("hint")
        self.keys.setTextFormat(Qt.TextFormat.RichText)
        self.send_btn = QPushButton("Send approved")
        self.send_btn.setObjectName("primary")
        self.send_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.send_btn.clicked.connect(lambda: self._do(self.model.ask_send if self.model.mode != "confirm" else self.model.send))
        foot.addWidget(self.keys, 1)
        foot.addWidget(self.send_btn)
        lay.addLayout(foot)

        self.filter = _TypingFilter(self)
        self.editor.installEventFilter(self.filter)
        self.name_box.installEventFilter(self.filter)
        self.setFixedWidth(760)
        self.timer = QTimer(self, interval=1500, timeout=self._tick)
        self.model.on_change = lambda: None
        self.model.run_async = self._run_async
        self._async_done.connect(lambda cb: cb())

    def _run_async(self, fn, done) -> None:  # noqa: ANN001
        """Slow work (restarting the preview) off the UI thread; `done` runs back on it."""
        def work() -> None:
            try:
                r, e = fn(), None
            except Exception as ex:  # noqa: BLE001
                r, e = None, ex
            self._async_done.emit(lambda: (done(r, e), self.render() if self.isVisible() else None))

        threading.Thread(target=work, daemon=True, name="overlay-work").start()

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
        self.render()
        self._place(work)
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
        self.hide()
        if self.fg_hwnd:
            win32.restore_foreground(self.fg_hwnd)

    def _place(self, work: Optional[dict]) -> None:
        if work:
            tl = logical_point_for_physical(work["left"], work["top"])
            br = logical_point_for_physical(work["left"] + work["width"] - 1, work["top"] + work["height"] - 1)
            area = QRect(tl, br)
        else:
            area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.center().x() - self.width() // 2, max(area.top() + 24, area.center().y() - self.height() // 2))

    def _tick(self) -> None:
        if self.model.mode in TYPING:
            return  # never redraw under someone typing
        try:
            self.model.refresh()
        except Exception:  # noqa: BLE001
            return
        self.render()

    def _focus(self) -> None:
        if self.model.mode in ("edit", "answer"):
            self.editor.setFocus()
            c = self.editor.textCursor()
            c.movePosition(c.MoveOperation.End)
            self.editor.setTextCursor(c)
        elif self.model.mode == "rename":
            self.name_box.setFocus()
            self.name_box.end(False)
        else:
            self.setFocus()

    # -------------------------------------------------------------- keys
    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if ev.key() == Qt.Key.Key_Escape:
            self.hide_overlay()
            return
        if self.model.mode in TYPING:
            return super().keyPressEvent(ev)
        name = KEYS.get(ev.key()) or (ev.text() or "").lower()
        if name and self.model.key(name, bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)):
            self.render()
            return
        super().keyPressEvent(ev)

    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802 - Tab switches views instead of moving focus
        if self.model.mode in TYPING:
            return super().focusNextPrevChild(_next)
        self.model.key("tab")
        self.render()
        return True

    def _do(self, fn) -> None:  # noqa: ANN001
        fn()
        self.render()

    # -------------------------------------------------------------- drawing
    def render(self) -> None:
        m = self.model
        st = m.state
        sess = st.get("session") or {}
        mode = m.mode
        self.name_box.setVisible(mode == "rename")
        self.name.setVisible(mode != "rename")
        if mode == "rename" and self.name_box.text() != (m.name_edit or ""):
            self.name_box.setText(m.name_edit or "")
        lock = f"  ·  {sess.get('branch')}" if sess.get("name_locked") else ""
        self.name.setText((sess.get("title") or "Review") + lock)
        counts = st.get("counts", {})
        cur, total = m.position
        self.progress.setText(f"Card {cur} of {total}" if m.card and mode in ("review", "edit") else "")
        self.status.setText(self._status_line(counts, st))
        card = m.card
        show_card = (mode in ("review", "edit", "rename") and card is not None) or (mode == "answer" and m.task is not None)
        self.card_box.setVisible(show_card)
        if show_card and mode == "answer":
            self._draw_answer(m.task)
        elif show_card:
            self._draw_card(card, mode == "edit")
        self.panel.setVisible(not show_card)
        if not show_card:
            self.panel.setText(self._panel_html(mode, st))
        self.msg.setText(m.message)
        self.msg.setVisible(bool(m.message))
        self.err.setText(m.error)
        self.err.setVisible(bool(m.error))
        n = m.approved_unsent
        self.send_btn.setText("Send now  (Enter)" if mode == "confirm" else f"Send approved ({n})")
        self.send_btn.setEnabled(mode == "confirm" or n > 0)
        self.keys.setText(self._keys_html(mode, card is not None))
        self._fit()
        self._focus()

    def _fit(self) -> None:
        """Height for the fixed width, including wrapped text (adjustSize would cap it at 2/3 of the screen)."""
        for inner in (self.card_box.layout(), self.findChild(QWidget, "root").layout()):
            inner.invalidate()
            inner.activate()
        self.card_box.updateGeometry()
        lay = self.layout()
        lay.invalidate()
        lay.activate()
        h = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.resize(self.width(), max(h, lay.totalMinimumSize().height()))

    def _status_line(self, counts: dict, st: dict) -> str:
        parts = []
        for key, label, color in (("approved", "approved, not sent", MUTED), ("sending", "sending", ACCENT), ("sent", "sent", ACCENT),
                                  ("working", "working", ACCENT), ("checking", "checking", ACCENT), ("ready", "ready", SUCCESS),
                                  ("needs_you", "need you", WARN)):
            if counts.get(key):
                parts.append(f"<span style='color:{color}'>{counts[key]} {label}</span>")
        if st.get("ready_to_refresh"):
            parts.append(f"<b style='color:{SUCCESS}'>Ready to refresh</b>")
        if st.get("incoming"):
            parts.append(f"{st['incoming']} more on the way (transcribing)")
        runs = [r for r in st.get("runs", []) if r["state"] in ("starting", "running", "checking")]
        if runs and runs[0].get("stage"):
            parts.append(f"<span style='color:{ACCENT}'>Agent: {_esc(runs[0]['stage'][:60])}</span>")
        agent = (st.get("project") or {}).get("agent_label")
        if agent and agent != "No agent":
            parts.append(f"→ {_esc(agent)}")
        return "  ·  ".join(parts)

    def _draw_answer(self, t: dict) -> None:
        self.type_tag.setText(t["code"] or "Task")
        self.origin_tag.setText("The agent needs you")
        self.image.hide()
        self.title.setText(t["title"])
        self.title.show()
        self.desc.setText(t.get("note") or "")
        self.desc.setVisible(bool(t.get("note")))
        self.quotes.hide()
        self.similar.setText("Your answer goes back with the task. Ctrl+Enter saves it, then S sends.")
        self.similar.show()
        self.editor.setVisible(True)
        self.editor.setPlaceholderText("Your answer…")
        text = self.model.answer_text()
        if self.editor.toPlainText() != text:
            self.editor.blockSignals(True)
            self.editor.setPlainText(text)
            self.editor.blockSignals(False)

    def _draw_card(self, c: dict, editing: bool) -> None:
        self.editor.setPlaceholderText("First line is the title")
        self.type_tag.setText(TYPE_LABEL.get(c["type"], c["type"]))
        origin = {"suggestion": "Suggested from what you said", "typed": "Your note", "quick": "Quick note", "ai": "Organised by local AI"}
        self.origin_tag.setText(origin.get(c["origin"], ""))
        img = c.get("image")
        pix = QPixmap(img["path"]) if img else QPixmap()
        if not pix.isNull():
            self.image.setPixmap(pix.scaled(718, 296, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.image.setToolTip("Possibly related: taken near this remark" if img.get("possibly_related") else "")
        self.image.setVisible(not pix.isNull())
        self.title.setVisible(not editing)
        self.desc.setVisible(not editing and bool(c["description"].strip()) and c["description"].strip() != c["title"].strip())
        self.title.setText(c["title"])
        self.desc.setText(c["description"][:600])
        q = [x for x in c.get("quotes", []) if x.strip() and x.strip() not in c["description"]][:3]
        self.quotes.setText("\n".join(f"“{x[:200]}”" for x in q))
        self.quotes.setVisible(bool(q) and not editing)
        sim = c.get("similar_to")
        note = c.get("uncertainty") if c.get("needs_context") else None
        if sim:
            self.similar.setText(f"Looks like {sim['code']} “{sim['title']}”, already sent. Approving adds this as evidence to it.")
        elif note:
            self.similar.setText(note)
        self.similar.setVisible(bool(sim or note) and not editing)
        self.editor.setVisible(editing)
        if editing:
            text = self.model.edit_text()
            if self.editor.toPlainText() != text:
                self.editor.blockSignals(True)
                self.editor.setPlainText(text)
                self.editor.blockSignals(False)

    def _panel_html(self, mode: str, st: dict) -> str:
        m = self.model
        if mode == "choose":
            rows = "".join(f"<p><b>{i}</b>  {_esc(c['project'])} · {_esc(c['title'])}</p>" for i, c in enumerate(m.choices, 1))
            return f"<p>Which session do you want to review?</p>{rows}"
        if mode == "confirm" and m.plan:
            p = m.plan
            items = "".join(f"<li>{_esc(t['title'])}{' <i>(follow-up to ' + t['follow_up_of'] + ')</i>' if t['follow_up_of'] else ''}</li>"
                            for t in p["tasks"])
            merges = "".join(f"<li>{_esc(x['title'])} <i>→ added to {x['into']} as evidence</i></li>" for x in p["merges"])
            where = f"branch <b>{_esc(p['branch'])}</b>{' (new)' if p['branch_new'] else ''} in {_esc(p['repo'] or '')}"
            wait = "<p>It starts when the current batch finishes.</p>" if p["queued_behind_current"] else ""
            head = f"<p><b>Send {p['count']} task{'s' if p['count'] != 1 else ''} to {_esc(p['agent_label'])}</b> on {where}</p>" if p["count"] else ""
            return f"{head}<ul>{items}</ul>{'<p>Repeat reports:</p><ul>' + merges + '</ul>' if merges else ''}{wait}"
        tasks = m.tasks
        rows = []
        for t in tasks:
            color = PILL.get(t["state"], MUTED)
            picked = t["id"] == m.task_id
            mark = f"<span style='color:{ACCENT}'>▶</span> " if picked else "&nbsp;&nbsp;&nbsp;"
            note = t.get("note") or ""
            if t["state"] == "ready" and not t.get("live") and st.get("project", {}).get("has_preview"):
                note = "Preview isn't serving this yet. " + note
            note_html = f"<br><span style='color:{MUTED}'>{_esc(note[:300]).replace(chr(10), '<br>')}</span>" if note and t["state"] in ("needs_you", "ready", "merged", "checking") else ""
            rows.append(f"<p>{mark}<b>{t['code'] or '·'}</b> {_esc(t['title'])} <span style='color:{color}'>● {_esc(t['label'])}</span>{note_html}</p>")
        pending = (st.get("counts") or {}).get("pending", 0)
        if not st:
            return f"<p>{_esc(m.message) or 'Nothing to review.'}</p>"
        head = "<p><b>All caught up.</b> No cards waiting.</p>" if not pending else ""
        return head + ("".join(rows) if rows else "<p style='color:%s'>Nothing sent from this session yet.</p>" % MUTED)

    def _keys_html(self, mode: str, has_card: bool) -> str:
        def k(key: str, label: str) -> str:
            return f"<b style='color:{TEXT}'>{key}</b> {label}"
        if mode in ("edit", "answer"):
            return "   ".join([k("Ctrl+Enter", "save"), k("Esc", "hide (keeps your text)")])
        if mode == "tasks":
            t = self.model.task
            keys = [k("J/K", "pick")]
            if t and t["state"] in ("ready", "needs_you"):
                keys += [k("Y", "works"), k("F", "still broken")]
            if t and t["state"] == "needs_you":
                keys += [k("E", "answer"), k("A", "send again")]
            keys += [k("R", "restart preview"), k("S", "send"), k("Tab", "cards"), k("Esc", "hide")]
            return "   ".join(keys)
        if mode == "rename":
            return "   ".join([k("Enter", "save name"), k("Esc", "hide")])
        if mode == "confirm":
            return "   ".join([k("Enter", "send"), k("Backspace", "back"), k("Esc", "hide")])
        if mode == "choose":
            return "   ".join([k("1-9", "pick"), k("Esc", "hide")])
        keys = [k("A", "approve"), k("D", "dismiss"), k("E", "edit"), k("U", "undo")] if has_card and mode == "review" else [k("U", "undo")]
        keys += [k("S", "send"), k("N", "rename"), k("Tab", "tasks" if mode == "review" else "cards"), k("Esc", "hide")]
        return "   ".join(keys)
