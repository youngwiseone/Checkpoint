"""State and key handling for the review overlay, without Qt (so it can be tested).

Modes
  review   one card at a time: A approve, D dismiss, E edit, U undo, S send approved, N rename, R restart preview,
           Tab tasks. Reviewing the last card goes straight to the send step when something is approved.
  edit     typing in the card's text box: letter keys are text; Ctrl+Enter saves
  rename   typing the session name: Enter saves
  confirm  about to send: Enter or S sends, Backspace goes back
  tasks    where the session's tasks stand, J/K to pick one: Y it works, F still broken, E answer the agent's
           question, A send again, R restart preview
  answer   typing an answer to the agent's question: Ctrl+Enter saves it and puts the task back to approved
  choose   which session to review, when that's unclear (1-9 picks)
Esc hides the overlay from any mode. Nothing is lost: the mode, the current card and any
unsaved edit text are kept and come back when the overlay reopens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import agents, flow
from .review import ReviewError


def _inline(fn, done) -> None:  # noqa: ANN001
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001
        done(None, e)
        return
    done(r, None)


@dataclass
class Action:
    kind: str  # approve | dismiss
    draft_id: str
    title: str


@dataclass
class OverlayModel:
    previews: Any = None
    session_id: Optional[str] = None
    state: dict = field(default_factory=dict)
    choices: list[dict] = field(default_factory=list)
    mode: str = "review"
    current_id: Optional[str] = None
    edits: dict[str, str] = field(default_factory=dict)  # unsaved edit text per card
    name_edit: Optional[str] = None
    history: list[Action] = field(default_factory=list)
    reviewed: int = 0
    message: str = ""
    error: str = ""
    plan: Optional[dict] = None
    task_id: Optional[str] = None  # selected in the tasks view
    on_change: Callable[[], None] = lambda: None
    # Runs slow work (restarting the preview); the overlay passes one that uses a thread. Tests run it inline.
    run_async: Callable[[Callable[[], Any], Callable[[Any, Optional[Exception]], None]], None] = field(
        default=lambda fn, done: _inline(fn, done))

    # -------------------------------------------------------------- loading
    def open(self, active_session_id: Optional[str], window: Optional[dict] = None) -> None:
        """Called each time the overlay is shown. Keeps progress if it's the same session."""
        t = flow.target(active_session_id, window)
        if t.get("choices"):
            if self.session_id and any(c["session_id"] == self.session_id for c in t["choices"]):
                flow.adopt_loose(self.session_id)
                self.refresh()
                return
            self.choices, self.mode = t["choices"], "choose"
            return
        sid = t.get("session_id")
        if not sid:
            self.session_id, self.state, self.mode = None, {}, "review"
            self.message = "Nothing to review yet. Capture something with F8 or F9 first."
            return
        if sid != self.session_id:
            self.select(sid)
        else:
            flow.adopt_loose(sid)  # cards captured outside a session since it was last open
            self.refresh()

    def select(self, session_id: str) -> None:
        self.session_id = session_id
        self.current_id, self.history, self.reviewed = None, [], 0
        self.mode, self.plan, self.name_edit = "review", None, None
        self.message = self.error = ""
        flow.adopt_loose(session_id)
        self.refresh()

    def refresh(self) -> None:
        if not self.session_id:
            return
        try:
            self.state = flow.state(self.session_id, self.previews)
        except agents.SendError:
            self.session_id, self.state = None, {}
            return
        ids = [c["id"] for c in self.cards]
        if self.current_id not in ids:
            self.current_id = ids[0] if ids else None
        if self.mode == "edit" and self.current_id not in self.edits:
            self.mode = "review"
        tids = [t["id"] for t in self.tasks]
        if self.task_id not in tids:
            self.task_id = tids[0] if tids else None

    # -------------------------------------------------------------- views
    @property
    def cards(self) -> list[dict]:
        return self.state.get("cards", [])

    @property
    def tasks(self) -> list[dict]:
        return [t for t in self.state.get("tasks", []) if t["state"] != "done" or t["id"] == self.task_id]

    @property
    def task(self) -> Optional[dict]:
        return next((t for t in self.tasks if t["id"] == self.task_id), None)

    @property
    def card(self) -> Optional[dict]:
        return next((c for c in self.cards if c["id"] == self.current_id), None)

    @property
    def approved_unsent(self) -> int:
        return self.state.get("approved_unsent", 0)

    @property
    def position(self) -> tuple[int, int]:
        """(this card's number, total in this sitting)."""
        return self.reviewed + 1, self.reviewed + len(self.cards)

    def edit_text(self) -> str:
        c = self.card
        if c is None:
            return ""
        if c["id"] in self.edits:
            return self.edits[c["id"]]
        return c["title"] + ("\n\n" + c["description"] if c["description"].strip() and c["description"].strip() != c["title"].strip() else "")

    # -------------------------------------------------------------- keys
    def key(self, key: str, ctrl: bool = False) -> bool:
        """Handle a key in a non-typing mode. Returns True when handled. `key` is a lowercase name
        ('a', 'enter', 'backspace', 'tab', '1', ...). Typing modes handle their own text."""
        self.error = ""
        k = key.lower()
        if self.mode == "choose":
            if k.isdigit() and 1 <= int(k) <= len(self.choices):
                self.select(self.choices[int(k) - 1]["session_id"])
                return True
            return False
        if self.mode == "confirm":
            if k in ("enter", "s"):
                self.send()
                return True
            if k in ("backspace", "d"):
                self.mode, self.plan = "review", None
                return True
            return False
        if self.mode == "tasks":
            actions = {"tab": self.show_cards, "t": self.show_cards, "s": self.ask_send, "j": lambda: self._step_task(1),
                       "down": lambda: self._step_task(1), "k": lambda: self._step_task(-1), "up": lambda: self._step_task(-1),
                       "y": self.works, "f": self.still_broken, "e": self.begin_answer, "a": self.send_again,
                       "r": self.restart_preview}
        elif self.mode == "review":
            actions = {"a": self.approve, "d": self.dismiss, "e": self.begin_edit, "u": self.undo, "s": self.ask_send,
                       "n": self.begin_rename, "tab": self.show_tasks, "t": self.show_tasks, "j": self.next, "k": self.prev,
                       "r": self.restart_preview}
        else:
            return False
        fn = actions.get(k)
        if fn is None:
            return False
        fn()
        return True

    def _guard(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except (ReviewError, agents.SendError, ValueError) as e:
            self.error = str(e)
        self.refresh()
        self.on_change()

    def _advance_from(self, draft_id: str) -> None:
        ids = [c["id"] for c in self.cards]
        if draft_id in ids:
            i = ids.index(draft_id)
            rest = ids[i + 1:] + ids[:i]
            self.current_id = rest[0] if rest else None

    def approve(self) -> None:
        c = self.card
        if c is None:
            return
        self._advance_from(c["id"])
        self._guard(lambda: flow.approve(c["id"]))
        if not self.error:
            self.history.append(Action("approve", c["id"], c["title"]))
            self.reviewed += 1
            self.message = f"Approved “{c['title']}”"
            if c.get("similar_to"):
                self.message += f" · looks like {c['similar_to']['code']}, so it'll be added there as evidence"
            self._offer_send()

    def dismiss(self) -> None:
        c = self.card
        if c is None:
            return
        self._advance_from(c["id"])
        self._guard(lambda: flow.dismiss(c["id"]))
        if not self.error:
            self.history.append(Action("dismiss", c["id"], c["title"]))
            self.reviewed += 1
            self.edits.pop(c["id"], None)
            self.message = f"Dismissed “{c['title']}” · U to undo"
            self._offer_send()

    def _offer_send(self) -> None:
        """After the last card, go straight to the send step (Backspace goes back)."""
        if self.cards or not self.approved_unsent:
            return
        try:
            plan = agents.plan(self.session_id)
        except agents.SendError:
            return
        if plan["problems"]:
            self.message += " · all reviewed. Set up the project in Settings → Projects to send it."
            return
        if plan["count"] or plan["merges"]:
            self.plan, self.mode = plan, "confirm"

    def undo(self) -> None:
        if not self.history:
            self.message = "Nothing to undo"
            return
        a = self.history.pop()
        self._guard(lambda: flow.undo(a.draft_id))
        if not self.error:
            self.current_id = a.draft_id
            self.reviewed = max(0, self.reviewed - 1)
            self.message = f"Back: “{a.title}”"
        else:
            self.history.append(a)

    def next(self) -> None:
        self._step(1)

    def prev(self) -> None:
        self._step(-1)

    def _step(self, d: int) -> None:
        ids = [c["id"] for c in self.cards]
        if self.current_id in ids and ids:
            self.current_id = ids[(ids.index(self.current_id) + d) % len(ids)]

    # -------------------------------------------------------------- typing modes
    def begin_edit(self) -> None:
        if self.card is None:
            return
        self.edits.setdefault(self.card["id"], self.edit_text())
        self.mode = "edit"

    def typed(self, text: str) -> None:
        """Keeps what's in the text box, so hiding the overlay never loses it."""
        if self.mode == "edit" and self.current_id:
            self.edits[self.current_id] = text
        elif self.mode == "answer" and self.task_id:
            self.edits["answer:" + self.task_id] = text
        elif self.mode == "rename":
            self.name_edit = text

    def save_edit(self) -> None:
        c = self.card
        if c is None or self.mode != "edit":
            return
        text = self.edits.get(c["id"], "").strip()
        title, _, rest = text.partition("\n")
        if not title.strip():
            self.error = "The first line is the title, so it can't be empty."
            return
        self._guard(lambda: flow.edit(c["id"], title.strip(), rest.strip()))
        if not self.error:
            self.edits.pop(c["id"], None)
            self.mode = "review"
            self.message = "Saved · A to approve"

    def cancel_edit(self) -> None:
        if self.current_id:
            self.edits.pop(self.current_id, None)
        self.mode = "review"

    def begin_rename(self) -> None:
        sess = self.state.get("session") or {}
        if sess.get("name_locked"):
            self.error = f"The name is locked to {sess.get('branch')} since the first send."
            return
        self.name_edit = self.name_edit if self.name_edit is not None else sess.get("title", "")
        self.mode = "rename"

    def save_rename(self) -> None:
        name = (self.name_edit or "").strip()
        if not name:
            self.error = "The name can't be empty."
            return
        self._guard(lambda: agents.rename(self.session_id, name))
        if not self.error:
            self.name_edit = None
            self.mode = "review"

    def cancel_rename(self) -> None:
        self.name_edit = None
        self.mode = "review"

    # -------------------------------------------------------------- tasks: trying the result
    def show_tasks(self) -> None:
        self.mode = "tasks"

    def show_cards(self) -> None:
        self.mode = "review"

    def _step_task(self, d: int) -> None:
        ids = [t["id"] for t in self.tasks]
        if ids:
            i = ids.index(self.task_id) if self.task_id in ids else 0
            self.task_id = ids[(i + d) % len(ids)]

    def works(self) -> None:
        t = self.task
        if t is None:
            return
        self._guard(lambda: agents.mark_works(t["id"]))
        if not self.error:
            self.message = f"{t['code']} done"
            self._step_task(1)

    def still_broken(self) -> None:
        t = self.task
        if t is None:
            return
        made: dict = {}
        self._guard(lambda: made.update(id=agents.still_broken(t["id"])))
        if made.get("id"):
            self.current_id = made["id"]
            self.edits[made["id"]] = f"Still broken: {t['title']}\n\n"
            self.mode = "edit"
            self.message = f"Say what's still wrong with {t['code']}, then Ctrl+Enter and A to approve it as a follow-up."

    def send_again(self) -> None:
        t = self.task
        if t is None:
            return
        self._guard(lambda: agents.resend(t["id"]))
        if not self.error:
            self.message = f"{t['code']} is approved again · S to send"

    def begin_answer(self) -> None:
        t = self.task
        if t is None or t["state"] != "needs_you":
            self.error = "Pick a task that needs you (J/K) to answer it."
            return
        self.edits.setdefault("answer:" + t["id"], "")
        self.mode = "answer"

    def answer_text(self) -> str:
        return self.edits.get("answer:" + (self.task_id or ""), "")

    def save_answer(self) -> None:
        t = self.task
        if t is None or self.mode != "answer":
            return
        text = self.answer_text()
        self._guard(lambda: agents.answer(t["id"], text))
        if not self.error:
            self.edits.pop("answer:" + t["id"], None)
            self.mode = "tasks"
            self.message = f"Answered {t['code']} · S to send it back to the agent"

    def restart_preview(self) -> None:
        if not self.session_id:
            return
        self.message = "Restarting the preview from this session's working copy…"
        sid = self.session_id

        def done(_r, e) -> None:  # noqa: ANN001
            self.error = str(e) if e else ""
            self.message = "" if e else "Preview restarted with the latest changes"
            self.refresh()
            self.on_change()

        self.run_async(lambda: agents.restart_preview(sid, self.previews), done)

    # -------------------------------------------------------------- sending
    def ask_send(self) -> None:
        if not self.session_id:
            return
        try:
            self.plan = agents.plan(self.session_id)
        except agents.SendError as e:
            self.error = str(e)
            return
        if self.plan["problems"]:
            self.error = self.plan["problems"][0]
            self.plan = None
            return
        if not self.plan["count"] and not self.plan["merges"]:
            self.error = "Nothing approved to send yet. Press A on a card to approve it."
            self.plan = None
            return
        self.mode = "confirm"

    def send(self) -> None:
        res: dict = {}

        def go() -> None:
            res.update(agents.send(self.session_id))

        self._guard(go)
        self.plan = None
        if self.error:
            self.mode = "review"
            return
        parts = []
        if res.get("count"):
            parts.append(f"Sending {res['count']} task{'s' if res['count'] != 1 else ''} to {res.get('agent_label', 'the agent')} on {res['branch']}")
        if res.get("merged"):
            parts.append(f"{res['merged']} repeat report{'s' if res['merged'] != 1 else ''} added as evidence")
        self.message = " · ".join(parts)
        self.mode = "tasks" if not self.cards else "review"
