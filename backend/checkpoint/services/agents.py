"""Send a session's approved tasks to the project's coding agent and follow them to Ready.

Status of a task (WorkItem.agent_state), separate from its work status:
  None       approved, not sent yet
  sending    handed to Checkpoint's queue; the agent hasn't accepted it yet
  sent       the agent process started and accepted the batch
  working    the agent reported it's working on this task
  checking   the agent says it's done; Checkpoint is running checks and restarting the preview
  ready      checks passed and the preview was restarted from a commit that contains the change
  needs_you  blocked: a question from the agent, failed checks, or the agent couldn't run

Each session has one branch in its own working copy (see workspace.py). Batches in a session
run one at a time; later batches resume the same agent conversation where the agent supports
it. Nothing is ever pushed or merged automatically.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select

from ..config import paths
from ..db import read_session, write_session
from ..events import hooks, notices
from ..models import AgentRun, EvidenceLink, Project, Session, WorkItem, utcnow
from ..settings_store import get_settings
from ..workers import Worker
from . import naming
from . import workspace as ws
from .preview import CHECK_TIMEOUT_S, SETUP_TIMEOUT_S, PreviewManager, run_command

log = logging.getLogger(__name__)

AGENT_LABELS = {"claude": "Claude Code", "codex": "Codex"}
IN_FLIGHT = ("sending", "sent", "working", "checking")
FINISHED = ("ready", "needs_you")
MCP_NAME = "checkpoint_run"


class SendError(ValueError):
    pass


def task_code(n: Optional[int]) -> str:
    return f"T-{n}" if n else ""


# ------------------------------------------------------------------ finding the agent
def _candidates(agent: str) -> list[Path]:
    home = Path.home()
    if agent == "claude":
        out = [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude"]
        bundled = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code"
        if bundled.is_dir():  # the Claude desktop app keeps versioned copies; newest first
            vers = sorted((p for p in bundled.iterdir() if p.is_dir()), key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split(".")],
                          reverse=True)
            out += [v / "claude.exe" for v in vers]
        return out
    if agent == "codex":
        npm = Path(os.environ.get("APPDATA", "")) / "npm"
        return [npm / "codex.cmd", home / ".local" / "bin" / "codex.exe"]
    return []


def find_agent(agent: str) -> Optional[str]:
    if agent not in AGENT_LABELS:
        return None
    custom = getattr(get_settings().agents, f"{agent}_path", "") or ""
    if custom.strip():
        return custom.strip() if Path(custom.strip()).is_file() else None
    found = shutil.which(agent)
    if found:
        return found
    return next((str(p) for p in _candidates(agent) if p.is_file()), None)


def agent_status() -> list[dict]:
    return [{"id": a, "name": label, "path": find_agent(a)} for a, label in AGENT_LABELS.items()]


# ------------------------------------------------------------------ repeat reports
def _tokens(title: str) -> set[str]:
    return {w.lower() for w in naming.short_phrase(title, 12).split() if len(w) > 2}


def similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if len(ta & tb) < 2:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.5


def _copy_evidence(s, src: WorkItem, dst: WorkItem) -> None:  # noqa: ANN001
    have = {(l.capture_id, l.segment_id, l.note_id) for l in s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == dst.id)).all()}
    for l in s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == src.id)).all():
        key = (l.capture_id, l.segment_id, l.note_id)
        if key not in have:
            s.add(EvidenceLink(work_item_id=dst.id, capture_id=l.capture_id, segment_id=l.segment_id, note_id=l.note_id,
                               confidence=l.confidence, role="context", attached_by="system"))
            have.add(key)


# ------------------------------------------------------------------ selecting what to send
def _problems(s, sess: Session, proj: Project) -> list[str]:  # noqa: ANN001
    out = []
    if sess.is_demo or proj.is_demo:
        out.append("Demo sessions can't be sent to an agent.")
    if not proj.repo_path:
        out.append(f"Link “{proj.name}” to its Git repository first (Settings → Projects).")
    if proj.default_agent not in AGENT_LABELS:
        out.append(f"Choose a default agent for “{proj.name}” (Settings → Projects).")
    elif not find_agent(proj.default_agent):
        out.append(f"{AGENT_LABELS[proj.default_agent]} isn't installed on this PC (or set its path in Settings → Projects).")
    return out


def _unsent(s, session_id: str) -> list[WorkItem]:  # noqa: ANN001
    return s.scalars(select(WorkItem).where(WorkItem.session_id == session_id, WorkItem.agent_state.is_(None),
                                            WorkItem.duplicate_of_id.is_(None), WorkItem.origin == "local",
                                            WorkItem.work_status.in_(("open", "in_progress")))
                     .order_by(WorkItem.created_at)).all()


def _selection(s, session_id: str) -> tuple[list[WorkItem], list[tuple[WorkItem, WorkItem]], list[tuple[WorkItem, WorkItem]]]:  # noqa: ANN001
    """(to send, repeats merged into an in-flight task, follow-ups of a finished task)."""
    sent = s.scalars(select(WorkItem).where(WorkItem.session_id == session_id, WorkItem.agent_state.is_not(None))
                     .order_by(WorkItem.task_number)).all()
    to_send, merges, follow = [], [], []
    for w in _unsent(s, session_id):
        match = next((x for x in sent if similar(w.title, x.title)), None)
        if match and match.agent_state in IN_FLIGHT:
            merges.append((w, match))
            continue
        if match:
            follow.append((w, match))
        to_send.append(w)
    return to_send, merges, follow


def _branch_taken(s, repo: str):  # noqa: ANN001, ANN202
    owned = set(s.scalars(select(Session.branch).where(Session.branch.is_not(None))).all())
    return lambda b: b in owned or ws.branch_exists(repo, b)


def plan(session_id: str) -> dict:
    """What 'Send approved' would do right now: shown before anything is sent."""
    with read_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise SendError("Session not found")
        proj = s.get(Project, sess.project_id)
        to_send, merges, follow = _selection(s, session_id)
        problems = _problems(s, sess, proj)
        branch = sess.branch
        if not branch and not problems:
            try:
                branch = naming.branch_for(sess.title, _branch_taken(s, proj.repo_path))
            except ws.GitError as e:
                problems.append(f"The project's repository can't be read: {e}")
        busy = s.scalars(select(AgentRun.id).where(AgentRun.session_id == session_id,
                                                   AgentRun.state.in_(("queued", "starting", "running", "checking")))).first()
        fu = {w.id: m for w, m in follow}
        return {
            "count": len(to_send),
            "tasks": [{"id": w.id, "title": w.title, "type": w.type,
                       "follow_up_of": task_code(fu[w.id].task_number) if w.id in fu else None} for w in to_send],
            "merges": [{"id": w.id, "title": w.title, "into": task_code(m.task_number), "into_title": m.title} for w, m in merges],
            "agent": proj.default_agent, "agent_label": AGENT_LABELS.get(proj.default_agent, "No agent"),
            "branch": branch, "branch_new": not sess.branch, "name": sess.title, "repo": proj.repo_path,
            "queued_behind_current": bool(busy), "problems": problems,
        }


def send(session_id: str) -> dict:
    """Queue every approved, unsent task in the session as one batch. Idempotent per task."""
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise SendError("Session not found")
        proj = s.get(Project, sess.project_id)
        problems = _problems(s, sess, proj)
        if problems:
            raise SendError(problems[0])
        to_send, merges, follow = _selection(s, session_id)
        for w, orig in merges:
            _copy_evidence(s, w, orig)
            w.duplicate_of_id = orig.id
            w.agent_note = f"Reported again; added to {task_code(orig.task_number)} as evidence."
            orig.agent_note = ((orig.agent_note + "\n") if orig.agent_note else "") + f"Reported again: “{w.title}”."
        if not to_send:
            if merges:
                return {"count": 0, "merged": len(merges), "run_id": None, "branch": sess.branch}
            raise SendError("Nothing approved to send. Approve tasks first (A in the review overlay).")
        for w, orig in follow:
            w.duplicate_of_id = orig.id
        if not sess.name_locked:
            if not sess.name_edited:
                _apply_suggested_name(s, sess)
            sess.name_locked = True
        if not sess.branch:
            sess.branch = naming.branch_for(sess.title, _branch_taken(s, proj.repo_path))
        n = s.scalar(select(func.max(WorkItem.task_number)).where(WorkItem.session_id == session_id)) or 0
        run = AgentRun(session_id=session_id, project_id=proj.id, agent=proj.default_agent, state="queued",
                       stage="Waiting to start", item_ids=[w.id for w in to_send])
        s.add(run)
        s.flush()
        for w in to_send:
            n += 1
            w.task_number = n
            w.agent_state = "sending"
            w.agent_run_id = run.id
            w.agent_note = None
            if w.work_status == "open":
                w.work_status = "in_progress"
        result = {"count": len(to_send), "merged": len(merges), "run_id": run.id, "branch": sess.branch,
                  "agent_label": AGENT_LABELS[proj.default_agent]}
    hooks.emit_state()
    _wake()
    return result


def resend(item_id: str) -> dict:
    """A task that needs you goes back to approved, to be sent with the next batch."""
    with write_session() as s:
        w = s.get(WorkItem, item_id)
        if w is None:
            raise SendError("Task not found")
        if w.agent_state not in ("needs_you", "ready"):
            raise SendError("Only tasks that need you (or are ready) can be sent again.")
        w.agent_state = None
        w.agent_run_id = None
        w.duplicate_of_id = None
        return {"ok": True, "session_id": w.session_id}


def dismiss_duplicate(item_id: str) -> None:
    """Undo a 'reported again' merge: the report becomes its own task again."""
    with write_session() as s:
        w = s.get(WorkItem, item_id)
        if w and w.duplicate_of_id and w.agent_state is None:
            w.duplicate_of_id = None
            w.agent_note = None


# ------------------------------------------------------------------ names
def _apply_suggested_name(s, sess: Session) -> None:  # noqa: ANN001
    from ..models import DraftItem

    rows = s.scalars(select(DraftItem).where(DraftItem.session_id == sess.id, DraftItem.review_state != "dismissed",
                                             DraftItem.merged_into_id.is_(None))).all()
    rows.sort(key=lambda d: (d.review_state != "approved", d.created_at))
    name = naming.suggest_name(d.title for d in rows)
    if name and name != sess.title:
        sess.title = name[:300]


def refresh_name(session_id: Optional[str]) -> None:
    """Keep refining the suggested name while reviewing, until it's edited or locked by a send."""
    if not session_id:
        return
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess and not sess.name_locked and not sess.name_edited:
            _apply_suggested_name(s, sess)


def rename(session_id: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise SendError("The name can't be empty")
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise SendError("Session not found")
        if sess.name_locked:
            raise SendError(f"The name is locked to branch {sess.branch} since the first send.")
        sess.title = name[:300]
        sess.name_edited = True
    hooks.emit_state()
    return {"title": name[:300]}


# ------------------------------------------------------------------ prompt
SYSTEM = (
    "You were started by Checkpoint to work on tasks the user captured while testing. Checkpoint tracks each task by its "
    "T-number, so report progress with the report_task tool as you go. Stay on the current branch: don't push, merge, "
    "rebase, reset or switch branches, and don't create other branches or worktrees. Checkpoint runs the project's "
    "checks and restarts the app preview after you finish."
)


def build_prompt(sess_title: str, branch: str, cwd: str, items: list[dict], handoff_code: str, follow_ups: dict[str, str],
                 check_command: str, resumed: bool) -> str:
    from .export import TYPE_LABEL

    lines = [("More tasks" if resumed else "Tasks") + f" from the Checkpoint session “{sess_title}”.",
             f"You're in the session's working copy ({cwd}) on branch {branch}. Every change for these tasks belongs on this branch.",
             ""]
    for it in items:
        lines.append(f"### {it['code']} [{TYPE_LABEL.get(it['type'], it['type'])}] {it['title']}")
        if it["id"] in follow_ups:
            lines.append(f"Reported again after {follow_ups[it['id']]} was finished, so that change may be incomplete. "
                         "Check it first and fix what's still wrong rather than starting over.")
        if it.get("description") and it["description"].strip() != it["title"].strip():
            lines.append(it["description"].strip())
        for e in it.get("excerpts", [])[:8]:
            prefix = "LATER WITHDRAWN BY: " if e["role"] == "withdrawal" else ""
            lines.append(f"> [{e['offset']}] {e['source']}: {prefix}{e['text']}")
        lines.append("")
    lines += [
        f"Screenshots and all evidence: call get_handoff with \"{handoff_code}\". Quotes are speech-to-text and can be wrong; "
        "\"possibly related\" screenshots were taken near the remark. Check everything against the code.",
        "",
        "How to work:",
        "- Plan first: group tasks that touch the same files or depend on each other; the rest are independent.",
        "- Hand independent tasks to subagents so they run in parallel. Do overlapping tasks one after another, in the "
        "order listed, so their changes build on each other instead of competing.",
        "- Subagents edit files but don't commit. You review each task's change and commit it on its own, one commit per "
        "task, with a message that starts with its T-number (for example \"T-3: ...\"). Commit one task at a time.",
        "- For bugs make the smallest change that fixes the cause; for features implement only what was asked.",
        f"- Verify each change: build and run the relevant tests{f' (the project check is: {check_command})' if check_command else ''}.",
        "- report_task(task, \"working\") when you start a task; report_task(task, \"done\", note, commit) once it's committed; "
        "report_task(task, \"blocked\", question) if it needs a decision from the user; report_task(task, \"cannot_reproduce\", "
        "what you found) if you can't find the problem. Then carry on with the other tasks.",
        "- Finish with a short list: each T-number and what happened.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ agent commands
def _mcp_server(run_id: str) -> dict:
    from ..integrations import server_command

    c = server_command()
    c["env"] = {**c["env"], "CHECKPOINT_AGENT_RUN": run_id, "CHECKPOINT_DATA_DIR": str(paths().root)}
    return c


DENY = ["Bash(git push:*)", "Bash(git merge:*)", "Bash(git checkout:*)", "Bash(git switch:*)", "Bash(git rebase:*)",
        "Bash(git reset:*)", "Bash(git worktree:*)", "Bash(git branch:*)"]


def agent_command(agent: str, exe: str, access: str, cwd: str, run_id: str, resume_id: Optional[str],
                  check_command: str = "") -> tuple[list[str], Optional[Path]]:
    """The argv for a headless run (the prompt goes on stdin), plus a temp file to clean up."""
    if agent == "claude":
        cfg = paths().logs / "agents" / f"{run_id}.mcp.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"mcpServers": {MCP_NAME: _mcp_server(run_id)}}), encoding="utf-8")
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--mcp-config", str(cfg),
               "--append-system-prompt", SYSTEM, "--disallowedTools", *DENY]
        if access == "full":
            cmd += ["--dangerously-skip-permissions"]
        else:
            allowed = [f"mcp__{MCP_NAME}__report_task", f"mcp__{MCP_NAME}__get_handoff", f"mcp__{MCP_NAME}__get_item",
                       "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
                       "Bash(git show:*)"]
            first = check_command.strip().split("&&")[0].strip()
            if first and not re.search(r"[;|&<>`$]", first):
                allowed.append(f"Bash({first}:*)")
            cmd += ["--permission-mode", "acceptEdits", "--allowedTools", *allowed]
        if resume_id:
            cmd += ["--resume", resume_id]
        return cmd, cfg
    if agent == "codex":
        srv = _mcp_server(run_id)
        env = ", ".join(f"{k} = {json.dumps(v)}" for k, v in srv["env"].items())
        cmd = [exe, "exec", "--json", "-C", cwd,
               "-c", f"mcp_servers.{MCP_NAME}.command={json.dumps(srv['command'])}",
               "-c", f"mcp_servers.{MCP_NAME}.args={json.dumps(srv['args'])}",
               "-c", f"mcp_servers.{MCP_NAME}.env={{ {env} }}"]
        cmd += ["--dangerously-bypass-approvals-and-sandbox"] if access == "full" else ["--sandbox", "workspace-write"]
        cmd += ["-"]
        return cmd, None
    raise SendError("Unknown agent")


def _agent_env() -> dict:
    env = dict(os.environ)
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
        env.pop(k, None)  # Checkpoint may itself have been started from an agent session
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ------------------------------------------------------------------ progress reports (from the MCP tool)
REPORT_STATES = {"working": "working", "done": "checking", "blocked": "needs_you", "cannot_reproduce": "needs_you"}


def report(run_id: str, task: str, status: str, note: str = "", commit: str = "") -> str:
    status = (status or "").strip().lower()
    if status not in REPORT_STATES:
        return "Unknown status. Use working, done, blocked or cannot_reproduce."
    m = re.search(r"(\d+)", task or "")
    if not m:
        return "Give the task's T-number, for example T-2."
    with write_session() as s:
        run = s.get(AgentRun, run_id)
        if run is None:
            return "This agent run isn't known to Checkpoint."
        w = s.scalars(select(WorkItem).where(WorkItem.session_id == run.session_id, WorkItem.task_number == int(m.group(1)))).first()
        if w is None:
            return f"There's no task T-{m.group(1)} in this session."
        if w.agent_state not in IN_FLIGHT:
            return f"T-{w.task_number} isn't in progress (it's {w.agent_state or 'not sent'}), so the report was ignored."
        if run.accepted_at is None:
            _accept(s, run, None)
        w.agent_state = REPORT_STATES[status]
        w.agent_run_id = run.id
        note = (note or "").strip()[:2000]
        if status == "cannot_reproduce":
            note = "Couldn't reproduce. " + note
        elif status == "done":
            note = note or "Done; Checkpoint is checking it."
            if commit.strip():
                w.agent_commit = commit.strip()[:64]
        w.agent_note = note or None
    hooks.emit_state()
    return f"Recorded: T-{m.group(1)} {status}."


def _accept(s, run: AgentRun, agent_session_id: Optional[str]) -> None:  # noqa: ANN001
    run.state = "running"
    run.stage = "Working"
    run.accepted_at = utcnow()
    if agent_session_id:
        run.agent_session_id = agent_session_id
        sess = s.get(Session, run.session_id)
        if sess is not None and run.agent == "claude":
            sess.agent_session_id = agent_session_id
    for w in s.scalars(select(WorkItem).where(WorkItem.agent_run_id == run.id, WorkItem.agent_state == "sending")).all():
        w.agent_state = "sent"
        w.sent_at = utcnow()


# ------------------------------------------------------------------ the runner
_worker: Optional["AgentWorker"] = None


def _wake() -> None:
    if _worker:
        _worker.wake()


def _set_run(run_id: str, **fields: Any) -> None:
    with write_session() as s:
        r = s.get(AgentRun, run_id)
        if r:
            for k, v in fields.items():
                setattr(r, k, v)
    hooks.emit_state()


def _set_items(run_id: str, states: tuple[str, ...], new_state: str, note: Optional[str], **extra: Any) -> int:
    with write_session() as s:
        rows = s.scalars(select(WorkItem).where(WorkItem.agent_run_id == run_id, WorkItem.agent_state.in_(states))).all()
        for w in rows:
            w.agent_state = new_state
            if note is not None:
                w.agent_note = note
            for k, v in extra.items():
                setattr(w, k, v)
        n = len(rows)
    hooks.emit_state()
    return n


def recover_on_launch() -> None:
    """Runs that were going when Checkpoint stopped can't be followed any more; say so on each task."""
    with write_session() as s:
        for r in s.scalars(select(AgentRun).where(AgentRun.state.in_(("starting", "running", "checking")))).all():
            r.state = "failed"
            r.error = "Checkpoint stopped while this batch was running."
            r.finished_at = utcnow()
            for w in s.scalars(select(WorkItem).where(WorkItem.agent_run_id == r.id, WorkItem.agent_state.in_(IN_FLIGHT))).all():
                w.agent_state = "needs_you"
                w.agent_note = ("Checkpoint stopped while the agent was working. Check the branch; send it again if it isn't done.")


class AgentWorker(Worker):
    name = "agents"
    idle_sleep = 2.0

    def __init__(self, previews: PreviewManager, sessions=None) -> None:  # noqa: ANN001
        global _worker
        super().__init__()
        self.previews = previews
        self.sessions = sessions
        self._threads: dict[str, threading.Thread] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancel: set[str] = set()
        _worker = self

    def step(self) -> bool:
        self._threads = {k: t for k, t in self._threads.items() if t.is_alive()}
        self._promote_suggestions()
        with read_session() as s:
            busy = set(s.scalars(select(AgentRun.session_id).where(AgentRun.state.in_(("starting", "running", "checking")))).all())
            busy |= {sid for sid in self._threads}
            queued = s.scalars(select(AgentRun).where(AgentRun.state == "queued").order_by(AgentRun.created_at)).all()
            starting = [(r.id, r.session_id) for r in queued]
        started = False
        for rid, sid in starting:
            if sid in busy:
                _set_run(rid, stage="Waiting for this session's current batch to finish")
                continue
            busy.add(sid)
            t = threading.Thread(target=self._execute_safe, args=(rid,), daemon=True, name=f"agent-{rid[:6]}")
            self._threads[sid] = t
            t.start()
            started = True
        return started

    def _promote_suggestions(self) -> None:
        a = self.sessions.active if self.sessions else None
        if a is None:
            return
        from .suggestions import promote

        try:
            if promote(a.id):
                hooks.emit_state()
        except Exception:  # noqa: BLE001 - never let this stop sends
            log.exception("Turning markers into suggestions failed")

    def cancel(self, run_id: str) -> None:
        self._cancel.add(run_id)
        p = self._procs.get(run_id)
        if p and p.poll() is None:
            from .preview import kill_tree

            kill_tree(p.pid)
        with write_session() as s:
            r = s.get(AgentRun, run_id)
            if r and r.state == "queued":
                r.state, r.finished_at, r.error = "cancelled", utcnow(), "Stopped before it started."
                for w in s.scalars(select(WorkItem).where(WorkItem.agent_run_id == run_id, WorkItem.agent_state == "sending")).all():
                    w.agent_state, w.agent_run_id = None, None
        hooks.emit_state()

    def stop_all(self) -> None:
        for rid in list(self._procs):
            self.cancel(rid)

    # -------------------------------------------------------------- one batch
    def _execute_safe(self, run_id: str) -> None:
        try:
            self._execute(run_id)
        except Exception as e:  # noqa: BLE001
            log.exception("Agent run %s failed", run_id)
            _set_run(run_id, state="failed", error=str(e), finished_at=utcnow(), stage="Failed")
            _set_items(run_id, IN_FLIGHT, "needs_you", f"Checkpoint couldn't run this batch: {e}")
        finally:
            self._procs.pop(run_id, None)
            self._cancel.discard(run_id)

    def _execute(self, run_id: str) -> None:
        from . import handoff as ho
        from .export import _collect

        with read_session() as s:
            run = s.get(AgentRun, run_id)
            sess = s.get(Session, run.session_id)
            proj = s.get(Project, run.project_id)
            info = {"session_id": sess.id, "title": sess.title, "branch": sess.branch, "wt": sess.worktree_path,
                    "resume": sess.agent_session_id if run.agent == "claude" else None, "project_id": proj.id,
                    "project_name": proj.name, "repo": proj.repo_path, "base": proj.base_branch, "setup": proj.setup_command,
                    "check": proj.check_command, "preview": proj.preview_command, "url": proj.preview_url,
                    "agent": run.agent, "access": proj.agent_access}
            items = s.scalars(select(WorkItem).where(WorkItem.agent_run_id == run_id)).all()
            item_ids = [w.id for w in items]
            codes = {w.id: task_code(w.task_number) for w in items}
            follow_ups = {w.id: task_code(s.get(WorkItem, w.duplicate_of_id).task_number) for w in items
                          if w.duplicate_of_id and s.get(WorkItem, w.duplicate_of_id)}
        exe = find_agent(info["agent"])
        if not exe:
            raise RuntimeError(f"{AGENT_LABELS.get(info['agent'], info['agent'])} couldn't be found on this PC.")
        _set_run(run_id, state="starting", stage="Preparing the session's working copy")
        wt = self._ensure_worktree(run_id, info)
        head_before = ws.head(wt)
        handoff = ho.create(item_ids, True, True, f"Checkpoint session “{info['title']}”, branch {info['branch']}")
        detail, _ = _collect(item_ids, True)
        by_id = {d["id"]: d for d in detail}
        ordered = [{**by_id[i], "code": codes[i]} for i in item_ids if i in by_id]
        prompt = build_prompt(info["title"], info["branch"], wt, ordered, handoff["code"], follow_ups, info["check"],
                              resumed=bool(info["resume"]))
        with write_session() as s:
            r = s.get(AgentRun, run_id)
            r.prompt, r.head_before, r.handoff_id = prompt, head_before, handoff["id"]
            r.stage = f"Starting {AGENT_LABELS[info['agent']]}"
        cmd, tmp = agent_command(info["agent"], exe, info["access"], wt, run_id, info["resume"], info["check"])
        log_path = paths().logs / "agents" / f"{run_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _set_run(run_id, log_path=str(log_path))
        code, result, accepted = self._run_agent(run_id, cmd, prompt, wt, log_path)
        if tmp:
            try:
                tmp.unlink()
            except OSError:
                pass
        if run_id in self._cancel:
            _set_run(run_id, state="cancelled", stage="Stopped", finished_at=utcnow(), error="Stopped by you.")
            _set_items(run_id, ("sending",), "needs_you", "Stopped before the agent accepted it.")
            _set_items(run_id, ("sent", "working"), "needs_you", "Stopped by you while the agent was working.")
            self._check_and_preview(run_id, info, wt)
            return
        if not accepted:
            err = (result or {}).get("error") or f"The agent exited (code {code}) before accepting the tasks. See {log_path}."
            _set_run(run_id, state="failed", stage="Agent didn't start", finished_at=utcnow(), error=err)
            _set_items(run_id, ("sending",), "needs_you", err)
            return
        self._reconcile(run_id, info, wt, head_before, code, result or {})
        self._check_and_preview(run_id, info, wt)

    def _ensure_worktree(self, run_id: str, info: dict) -> str:
        wt = info["wt"]
        if wt and Path(wt).is_dir() and ws.current_branch(wt) == info["branch"]:
            return wt
        repo = ws.inspect_repo(info["repo"]).root
        dest = Path(wt) if wt else ws.worktree_dir(info["project_name"], info["branch"])
        if ws.branch_exists(repo, info["branch"]) and ws.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{info['branch']}"], repo, check=False):
            ws.attach_worktree(repo, info["branch"], dest)
            base_commit = None
        else:
            base = info["base"] or ws.inspect_repo(repo).default_branch
            base_commit = ws.create_worktree(repo, info["branch"], base, dest)
        with write_session() as s:
            sess = s.get(Session, info["session_id"])
            sess.worktree_path = str(dest)
            if base_commit:
                sess.base_commit = base_commit
        if info["setup"].strip():
            _set_run(run_id, stage="Setting up the working copy")
            res = run_command(info["setup"], str(dest), SETUP_TIMEOUT_S, paths().logs / "agents" / f"{run_id}.setup.log")
            if not res.ok:
                raise RuntimeError(f"The project's setup command failed in the new working copy:\n{res.tail(15)}")
        info["wt"] = str(dest)
        return str(dest)

    def _run_agent(self, run_id: str, cmd: list[str], prompt: str, cwd: str, log_path: Path) -> tuple[Optional[int], Optional[dict], bool]:
        accepted = False
        result: Optional[dict] = None
        with log_path.open("a", encoding="utf-8") as logf:
            logf.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(cmd[:2])} in {cwd}\n")
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", env=_agent_env(),
                                        creationflags=ws.NO_WINDOW)
            except OSError as e:
                return None, {"error": f"Couldn't start the agent: {e}"}, False
            self._procs[run_id] = proc
            _set_run(run_id, pid=proc.pid, stage="Waiting for the agent to accept the tasks")
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except OSError:
                pass
            for line in proc.stdout:
                logf.write(line)
                logf.flush()
                ev = _parse(line)
                if ev is None:
                    continue
                if not accepted:
                    accepted = True
                    with write_session() as s:
                        _accept(s, s.get(AgentRun, run_id), ev.get("session_id") or ev.get("thread_id"))
                    hooks.emit_state()
                if ev.get("type") == "result":
                    result = ev
            code = proc.wait()
        return code, result, accepted

    def _reconcile(self, run_id: str, info: dict, wt: str, head_before: str, code: Optional[int], result: dict) -> None:
        """After the agent exits: match commits to tasks and flag anything it left unfinished."""
        summary = (result.get("result") or "").strip()
        failed = bool(result.get("is_error")) or (code not in (0, None) and not summary)
        problem = _explain_failure(info["agent"], summary) if failed else None
        head_after = ws.head(wt)
        branch_now = ws.current_branch(wt)
        commits = ws.commits_since(wt, head_before)
        dirty = ws.dirty_files(wt)
        _set_run(run_id, head_after=head_after, summary=summary[:8000] or None, state="checking",
                 stage="Checking the agent's work", error=problem)
        with write_session() as s:
            items = s.scalars(select(WorkItem).where(WorkItem.agent_run_id == run_id)).all()
            for w in items:
                mine = [c for c in commits if w.task_number in ws.task_numbers_in(c["subject"])]
                if branch_now != info["branch"]:
                    w.agent_state = "needs_you"
                    w.agent_note = f"The agent left the session branch (now on {branch_now or 'a detached HEAD'}). Check the working copy."
                    continue
                if w.agent_state in ("sent", "working"):
                    if mine:
                        w.agent_state = "checking"
                        w.agent_note = w.agent_note or f"Committed: {mine[0]['subject']}"
                    elif failed:
                        w.agent_state = "needs_you"
                        w.agent_note = problem
                    else:
                        w.agent_state = "needs_you"
                        w.agent_note = "The agent finished without committing or reporting on this task." + (f"\n\n{summary[:600]}" if summary else "")
                if w.agent_state == "checking" and mine and not w.agent_commit:
                    w.agent_commit = mine[0]["sha"]
                if w.agent_state == "checking" and dirty and not mine:
                    w.agent_state = "needs_you"
                    w.agent_note = "Reported done, but its changes aren't committed: " + ", ".join(dirty[:5])

    def _check_and_preview(self, run_id: str, info: dict, wt: str) -> None:
        with read_session() as s:
            waiting = s.scalars(select(WorkItem.id).where(WorkItem.agent_run_id == run_id, WorkItem.agent_state == "checking")).all()
        if not waiting:
            _finish(run_id)
            return
        if info["check"].strip():
            _set_run(run_id, stage="Running the project's checks")
            res = run_command(info["check"], wt, CHECK_TIMEOUT_S, paths().logs / "agents" / f"{run_id}.checks.log")
            if not res.ok:
                _set_items(run_id, ("checking",), "needs_you", f"The project's checks failed after this change:\n{res.tail(20)}")
                _finish(run_id)
                return
        head = ws.head(wt)
        if info["preview"].strip():
            _set_run(run_id, stage="Restarting the preview from the session's working copy")
            pv = self.previews.start(info["session_id"], info["project_id"], wt, info["preview"], info["url"])
            if pv.state != "running":
                _set_items(run_id, ("checking",), "needs_you", f"Checks passed, but the preview didn't start: {pv.error}")
                _finish(run_id)
                return
            note = f"Checks passed · preview restarted at {head[:8]}"
        else:
            note = f"Checks passed at {head[:8]} · no preview command set up, so restart the app yourself"
        with write_session() as s:
            for w in s.scalars(select(WorkItem).where(WorkItem.agent_run_id == run_id, WorkItem.agent_state == "checking")).all():
                w.agent_state = "ready"
                w.agent_commit = w.agent_commit or head
                w.agent_note = note if not w.agent_note or w.agent_note.startswith(("Done", "Committed")) else f"{w.agent_note}\n{note}"
        _finish(run_id)
        n = len(waiting)
        hooks.emit_toast("success", f"{n} task{'s' if n != 1 else ''} ready to refresh · {info['title']}")


def _explain_failure(agent: str, summary: str) -> str:
    low = summary.lower()
    if "not logged in" in low or "/login" in low or "please log in" in low or "authentication" in low:
        return (f"{AGENT_LABELS.get(agent, agent)} isn't signed in on this PC. Open a terminal, run "
                f"`{'claude' if agent == 'claude' else 'codex login'}` and sign in once, then send the task again.")
    return f"The agent stopped with an error before finishing this: {summary[:500] or 'no details'}"


def _finish(run_id: str) -> None:
    with write_session() as s:
        r = s.get(AgentRun, run_id)
        if r and r.state not in ("cancelled", "failed"):
            r.state = "failed" if r.error else "done"
            r.stage = "Stopped with an error" if r.error else "Finished"
            r.finished_at = utcnow()
        needs = s.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.agent_run_id == run_id,
                                                                          WorkItem.agent_state == "needs_you")) or 0
    if needs:
        hooks.emit_toast("warning", f"{needs} task{'s' if needs != 1 else ''} need{'s' if needs == 1 else ''} you. Press Ctrl+F9 to see why.")
        notices.push("warning", f"{needs} task(s) need you after the agent's run.", "agents")
    hooks.emit_state()
    _wake()


def _parse(line: str) -> Optional[dict]:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    return ev if isinstance(ev, dict) else None


# ------------------------------------------------------------------ views
STATE_LABELS = {None: "Approved", "sending": "Sending", "sent": "Sent to agent", "working": "Working", "checking": "Working",
                "ready": "Ready", "needs_you": "Needs you"}


def task_dict(s, w: WorkItem, previews: Optional[PreviewManager] = None) -> dict:  # noqa: ANN001
    dup = s.get(WorkItem, w.duplicate_of_id) if w.duplicate_of_id else None
    merged = dup is not None and w.agent_state is None
    state = "merged" if merged else (w.agent_state or "approved")
    live = bool(previews and w.agent_state == "ready" and previews.serves(w.session_id, w.agent_commit))
    return {"id": w.id, "code": task_code(w.task_number), "title": w.title, "type": w.type, "state": state,
            "label": f"Added to {task_code(dup.task_number)}" if merged else STATE_LABELS.get(w.agent_state, w.agent_state),
            "checking": w.agent_state == "checking", "note": w.agent_note, "commit": w.agent_commit, "live": live,
            "duplicate_of": task_code(dup.task_number) if dup else None, "run_id": w.agent_run_id,
            "sent_at": w.sent_at.isoformat() if w.sent_at else None, "work_status": w.work_status}


def run_dict(r: AgentRun) -> dict:
    return {"id": r.id, "state": r.state, "stage": r.stage, "agent": r.agent, "agent_label": AGENT_LABELS.get(r.agent, r.agent),
            "items": len(r.item_ids or []), "summary": r.summary, "error": r.error, "log_path": r.log_path,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "accepted_at": r.accepted_at.isoformat() if r.accepted_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


def session_flow(session_id: str, previews: Optional[PreviewManager] = None) -> dict:
    from ..models import DraftItem

    with read_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise SendError("Session not found")
        proj = s.get(Project, sess.project_id)
        items = s.scalars(select(WorkItem).where(WorkItem.session_id == session_id, WorkItem.origin == "local")
                          .order_by(WorkItem.task_number.is_(None), WorkItem.task_number, WorkItem.created_at)).all()
        tasks = [task_dict(s, w, previews) for w in items if w.work_status != "wont_do"]
        runs = s.scalars(select(AgentRun).where(AgentRun.session_id == session_id).order_by(AgentRun.created_at.desc()).limit(10)).all()
        pending = s.scalar(select(func.count()).select_from(DraftItem).where(DraftItem.session_id == session_id,
                                                                             DraftItem.review_state == "pending",
                                                                             DraftItem.merged_into_id.is_(None))) or 0
        counts: dict[str, int] = {"pending": pending}
        for t in tasks:
            counts[t["state"]] = counts.get(t["state"], 0) + 1
        preview = previews.status(session_id) if previews else {"state": "stopped"}
        ready = [t for t in tasks if t["state"] == "ready"]
        return {
            "session": {"id": sess.id, "title": sess.title, "name_locked": sess.name_locked, "name_edited": sess.name_edited,
                        "branch": sess.branch, "worktree_path": sess.worktree_path, "pr_url": sess.pr_url,
                        "state": sess.state, "project_id": sess.project_id},
            "project": {"id": proj.id, "name": proj.name, "agent": proj.default_agent,
                        "agent_label": AGENT_LABELS.get(proj.default_agent, "No agent"), "repo_path": proj.repo_path,
                        "base_branch": proj.base_branch, "has_preview": bool(proj.preview_command.strip()),
                        "problems": _problems(s, sess, proj)},
            "counts": counts,
            "tasks": tasks,
            "runs": [run_dict(r) for r in runs],
            "preview": preview,
            "ready_to_refresh": bool(ready) and all(t["live"] for t in ready) if proj.preview_command.strip() else bool(ready),
        }
