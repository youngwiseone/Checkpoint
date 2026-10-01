"""State for the project switcher (Shift+F9), without Qt (so it can be tested).

Modes
  list   your projects, most recently used first. Type to filter, Up/Down to pick, Enter starts a session there
         (ending the running one if it belongs to another project). The last row makes a new project.
  new    a new project, prefilled from the last active project (agent, access, commands, what to record, and a
         repo folder next to its repo when one matches the name). Enter creates it and starts a session.
Esc hides the switcher from either mode; a half-filled new project is kept for next time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import func, select

from ..db import read_session, write_session
from ..models import DraftItem, Project, Session
from ..settings_store import WatchRule, get_settings, update_settings
from .flow import project_for_window
from .projects import AGENT_LABEL, ProjectError, apply_patch
from .sessions import remembered_setup


# Programs that are almost never the thing being tested, so they're not suggested for a new project.
NOT_A_TARGET = {"explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "code.exe", "cursor.exe",
                "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "claude.exe", "discord.exe",
                "slack.exe", "teams.exe", "ms-teams.exe", "outlook.exe", "spotify.exe", "obs64.exe", "taskmgr.exe"}


def _inline(fn, done) -> None:  # noqa: ANN001
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001
        done(None, e)
        return
    done(r, None)


def _utc(when: Optional[datetime]) -> Optional[datetime]:
    return when.replace(tzinfo=timezone.utc) if when is not None and when.tzinfo is None else when


def ago(when: Optional[datetime], now: Optional[datetime] = None) -> str:
    if when is None:
        return "never used"
    s = max(0, int(((now or datetime.now(timezone.utc)) - _utc(when)).total_seconds()))
    if s < 60:
        return "just now"
    for size, unit in ((86400 * 7, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if s >= size:
            return f"{s // size}{unit} ago"
    return "just now"


def switch_session(sessions, project_id: str, mic: Optional[bool] = None, loopback: Optional[bool] = None) -> tuple[str, Optional[str]]:  # noqa: ANN001
    """Start a session in `project_id` with its remembered setup, first ending the running session if it belongs to
    another project. Returns (new session id, ended session id or None)."""
    a = sessions.active
    if a is not None and a.project_id == project_id:
        raise ValueError("A session is already running in this project.")
    req = remembered_setup(project_id)
    if mic is not None:
        req.mic.enabled = mic
    if loopback is not None:
        req.loopback.enabled = loopback
    ended = sessions.end() if a is not None else None
    return sessions.start(req), ended


def _folder_names(name: str) -> list[str]:
    n = name.strip()
    words = n.split()
    return list(dict.fromkeys([n, "".join(words), "-".join(words), "_".join(words), "-".join(words).lower()]))


def suggest_repo(parent: Optional[str], name: str) -> str:
    """A folder named like the project next to the last project's repo, if there is one."""
    if not parent or not name.strip():
        return ""
    base = Path(parent)
    if not base.is_dir():
        return ""
    wanted = {x.lower() for x in _folder_names(name)}
    try:
        for child in base.iterdir():
            if child.is_dir() and child.name.lower() in wanted:
                return str(child)
    except OSError:
        return ""
    return ""


@dataclass
class SwitcherModel:
    sessions: Any = None  # SessionManager
    mode: str = "list"
    query: str = ""
    rows: list[dict] = field(default_factory=list)
    index: int = 0
    running_pid: Optional[str] = None
    running_name: str = ""
    window: Optional[dict] = None
    draft: dict = field(default_factory=dict)
    template: Optional[dict] = None  # the project a new one is copied from
    repo_parent: Optional[str] = None
    repo_auto: bool = True  # the folder follows the name until it's edited
    repo_status: dict = field(default_factory=dict)  # {ok, text, branch}
    message: str = ""
    error: str = ""
    on_switched: Callable[[str, str, Optional[str]], None] = lambda pid, sid, ended: None
    run_async: Callable[[Callable[[], Any], Callable[[Any, Optional[Exception]], None]], None] = field(
        default=lambda fn, done: _inline(fn, done))
    _repo_token: int = 0

    # -------------------------------------------------------------- loading
    def open(self, window: Optional[dict] = None) -> None:
        """Called each time the switcher is shown. A half-filled new project is kept; the list starts fresh."""
        self.window = window
        self.message = self.error = ""
        self.load()
        if self.mode == "list":
            self.query = ""
            self.index = self._preferred()

    def load(self) -> None:
        a = self.sessions.active if self.sessions else None
        self.running_pid = a.project_id if a else None
        detected = project_for_window(self.window)
        last = get_settings().last_project_id
        with read_session() as s:
            projects = s.scalars(select(Project).where(Project.archived.is_(False), Project.is_demo.is_(False))).all()
            used = dict(s.execute(select(Session.project_id, func.max(Session.started_at)).group_by(Session.project_id)).all())
            pending = dict(s.execute(select(DraftItem.project_id, func.count()).where(
                DraftItem.review_state == "pending", DraftItem.merged_into_id.is_(None)).group_by(DraftItem.project_id)).all())
            rows = [{"id": p.id, "name": p.name, "last_used": _utc(used.get(p.id)), "ago": ago(used.get(p.id)),
                     "pending": pending.get(p.id, 0), "agent": p.default_agent,
                     "agent_label": AGENT_LABEL.get(p.default_agent, "") if p.default_agent != "none" else "",
                     "repo": Path(p.repo_path).name if p.repo_path else "",
                     "running": p.id == self.running_pid, "detected": p.id == detected, "last": p.id == last,
                     "settings": {k: getattr(p, k) for k in ("repo_path", "base_branch", "default_agent", "agent_access",
                                                            "setup_command", "check_command", "preview_command", "preview_url")}}
                    for p in projects]
        # Recording now first, then most recently used; never-used projects last, by name.
        rows.sort(key=lambda r: (not r["running"], r["last_used"] is None,
                                 -r["last_used"].timestamp() if r["last_used"] else 0, r["name"].lower()))
        self.rows = rows
        self.running_name = next((r["name"] for r in rows if r["running"]), "")

    def _preferred(self) -> int:
        """Switching usually means somewhere else: the project in the foreground, else the most recent other one."""
        rows = self.filtered
        for i, r in enumerate(rows):
            if r["detected"] and not r["running"]:
                return i
        for i, r in enumerate(rows):
            if not r["running"]:
                return i
        return 0

    # -------------------------------------------------------------- the list
    @property
    def filtered(self) -> list[dict]:
        q = self.query.strip().lower()
        if not q:
            return self.rows
        hits = [r for r in self.rows if q in r["name"].lower()]
        return sorted(hits, key=lambda r: not r["name"].lower().startswith(q))  # stable: recency within each group

    @property
    def count(self) -> int:
        """Rows including the "New project" row at the end."""
        return len(self.filtered) + 1

    @property
    def selected(self) -> Optional[dict]:
        rows = self.filtered
        return rows[self.index] if 0 <= self.index < len(rows) else None

    @property
    def on_new_row(self) -> bool:
        return self.index == len(self.filtered)

    @property
    def exact(self) -> bool:
        q = self.query.strip().lower()
        return any(r["name"].lower() == q for r in self.rows)

    def set_query(self, text: str) -> None:
        self.query = text
        self.error = ""
        self.index = 0 if self.filtered else len(self.filtered)

    def move(self, d: int) -> None:
        self.index = max(0, min(self.count - 1, self.index + d))

    def choose(self, index: Optional[int] = None) -> Optional[str]:
        """Enter or a click on a row. Returns a message when a session started (the switcher then hides)."""
        if index is not None:
            self.index = index
        if self.on_new_row:
            self.start_new(self.query)
            return None
        r = self.selected
        if r is None:
            return None
        if r["running"]:
            self.error = f"Already recording in {r['name']}."
            return None
        return self.switch(r["id"])

    def switch(self, pid: str, mic: Optional[bool] = None, loopback: Optional[bool] = None) -> Optional[str]:
        try:
            sid, ended = switch_session(self.sessions, pid, mic, loopback)
        except ValueError as e:
            self.error = str(e)
            return None
        name = next((r["name"] for r in self.rows if r["id"] == pid), "")
        self.on_switched(pid, sid, ended)
        return f"Recording in {name}" + (f" · ended the {self.running_name} session" if ended and self.running_name else "")

    # -------------------------------------------------------------- a new project
    def _template(self) -> Optional[dict]:
        """The last active project: the one recording now, else the last one used."""
        return (next((r for r in self.rows if r["running"]), None) or next((r for r in self.rows if r["last"]), None)
                or (self.rows[0] if self.rows else None))

    def start_new(self, name: str = "") -> None:
        self.mode, self.error, self.message = "new", "", ""
        if self.draft:  # kept from last time; a new name typed in the search replaces the old one
            if name.strip():
                self.set_field("name", name.strip())
            return
        t = self._template()
        self.template = t
        st = t["settings"] if t else {}
        setup = remembered_setup(t["id"] if t else "-")
        self.repo_parent = str(Path(st["repo_path"]).parent) if st.get("repo_path") else None
        self.repo_auto = True
        # The program in the foreground, if no project claims it yet: probably the new thing you're testing.
        w = self.window or {}
        exe = (w.get("exe") or "").strip()
        program = exe if exe and exe.lower() not in NOT_A_TARGET and project_for_window(w) is None else ""
        kind = "exe"
        self.draft = {
            "name": name.strip(), "repo_path": "", "base_branch": "",
            "default_agent": st.get("default_agent") or "none", "agent_access": st.get("agent_access") or "standard",
            "setup_command": st.get("setup_command") or "", "check_command": st.get("check_command") or "",
            "preview_command": st.get("preview_command") or "", "preview_url": st.get("preview_url") or "",
            "program": program, "program_kind": kind, "mic": setup.mic.enabled, "loopback": setup.loopback.enabled,
        }
        self.repo_status = {}
        self._follow_name()

    def back(self) -> None:
        """To the list, keeping the draft."""
        self.mode, self.error = "list", ""
        self.index = self._preferred()

    def discard_draft(self) -> None:
        self.draft, self.template, self.repo_status = {}, None, {}

    def set_field(self, key: str, value: Any) -> None:
        self.draft[key] = value
        self.error = ""
        if key == "name":
            self._follow_name()
        elif key == "repo_path":
            self.repo_auto = False
            self.check_repo()
        elif key == "program":
            self.draft["program_kind"] = "exe" if value.strip().lower().endswith(".exe") else "title"

    def _follow_name(self) -> None:
        if self.repo_auto:
            self.draft["repo_path"] = suggest_repo(self.repo_parent, self.draft.get("name", ""))
            self.check_repo()

    def check_repo(self) -> None:
        path = (self.draft.get("repo_path") or "").strip()
        self._repo_token += 1
        token = self._repo_token
        if not path:
            near = f" (looked next to {Path(self.repo_parent).name})" if self.repo_parent and self.repo_auto else ""
            self.repo_status = {"ok": None, "text": f"No repo folder{near}: cards work, sending to an agent needs one"}
            return
        self.repo_status = {"ok": None, "text": "Checking folder…"}

        def done(info, err) -> None:  # noqa: ANN001
            if token != self._repo_token:
                return  # typed on since
            if err is not None:
                self.repo_status = {"ok": False, "text": str(err)}
            else:
                self.repo_status = {"ok": True, "text": f"Git repo · {info.default_branch}", "branch": info.default_branch}

        from .workspace import inspect_repo

        self.run_async(lambda: inspect_repo(path), done)

    def create(self) -> Optional[str]:
        """Create the project and start a session in it. Returns the success message, or sets `error`."""
        d = self.draft
        name = (d.get("name") or "").strip()
        if not name:
            self.error = "Give the project a name"
            return None
        if any(r["name"].lower() == name.lower() for r in self.rows):
            self.error = f"There's already a project called {name}. Alt+← to pick it."
            return None
        if self.repo_status.get("ok") is False and d.get("repo_path", "").strip():
            self.error = self.repo_status["text"]
            return None
        patch = {k: d.get(k) for k in ("repo_path", "base_branch", "default_agent", "agent_access", "setup_command",
                                       "check_command", "preview_command", "preview_url")}
        if not (patch["base_branch"] or "").strip():
            patch["base_branch"] = self.repo_status.get("branch") or ""
        try:
            with write_session() as s:
                p = Project(name=name)
                s.add(p)
                s.flush()
                apply_patch(p, patch)
                pid = p.id
        except ProjectError as e:
            self.error = str(e)
            return None
        program = (d.get("program") or "").strip()
        if program:
            rules = [r.model_dump() for r in get_settings().app_watch.rules if r.project_id != pid]
            rule = WatchRule(project_id=pid, kind=d.get("program_kind") or "exe", match=program[:200])
            update_settings({"app_watch": {"rules": [*rules, rule.model_dump()]}})
        self.rows.append({"id": pid, "name": name, "running": False})
        msg = self.switch(pid, mic=bool(d.get("mic")), loopback=bool(d.get("loopback")))
        if msg is None:
            # The project exists now; only the session didn't start. Show it in the list.
            self.discard_draft()
            self.mode = "list"
            self.load()
            self.index = next((i for i, r in enumerate(self.filtered) if r["id"] == pid), 0)
            return None
        self.discard_draft()
        self.mode = "list"
        return f"Created {name} · " + msg
