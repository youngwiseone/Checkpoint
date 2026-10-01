"""Routes for the capture → review → send → ready loop (the same actions as the review overlay)."""

from __future__ import annotations

import webbrowser
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import read_session, write_session
from ..events import hooks
from ..models import AgentRun, Project, Session
from ..services import agents, flow
from ..services import review as rv
from ..services import workspace as ws

router = APIRouter(prefix="/api")


def core():  # noqa: ANN201
    from ..core import get_core

    return get_core()


def _bad(e: Exception, code: int = 400) -> HTTPException:
    return HTTPException(code, str(e))


# ------------------------------------------------------------------ setup
@router.get("/agents")
def list_agents() -> list[dict]:
    return agents.agent_status()


class RepoIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


@router.post("/repo/inspect")
def inspect_repo(body: RepoIn) -> dict:
    try:
        info = ws.inspect_repo(body.path)
    except ws.GitError as e:
        raise _bad(e) from e
    return {"root": info.root, "current_branch": info.current_branch, "default_branch": info.default_branch,
            "remote_url": info.remote_url, "branches": info.branches}


# ------------------------------------------------------------------ the loop
@router.get("/flow/target")
def overlay_target() -> dict:
    a = core().sessions.active
    window = None
    if a is None:
        try:
            from ..capture.win32 import foreground_window

            window = foreground_window()
        except Exception:  # noqa: BLE001
            window = None
    return flow.target(a.id if a else None, window)


@router.get("/sessions/{sid}/flow")
def session_flow(sid: str, adopt: bool = False) -> dict:
    try:
        if adopt:
            flow.adopt_loose(sid)
        return flow.state(sid, core().previews)
    except agents.SendError as e:
        raise HTTPException(404, str(e)) from e


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)


@router.post("/sessions/{sid}/name")
def rename(sid: str, body: NameIn) -> dict:
    try:
        return agents.rename(sid, body.name)
    except agents.SendError as e:
        raise _bad(e, 409) from e


@router.get("/sessions/{sid}/send-plan")
def send_plan(sid: str) -> dict:
    try:
        return agents.plan(sid)
    except agents.SendError as e:
        raise _bad(e) from e


@router.post("/sessions/{sid}/send")
def send(sid: str) -> dict:
    try:
        return agents.send(sid)
    except (agents.SendError, ws.GitError) as e:
        raise _bad(e, 409) from e


class CardEdit(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    description: str = ""


@router.post("/cards/{did}/{action}")
def card_action(did: str, action: str, body: Optional[CardEdit] = None) -> dict:
    try:
        if action == "approve":
            flow.approve(did)
        elif action == "dismiss":
            flow.dismiss(did)
        elif action == "undo":
            flow.undo(did)
        elif action == "edit":
            if body is None or not (body.title or "").strip():
                raise HTTPException(400, "The title can't be empty")
            flow.edit(did, body.title, body.description)
        else:
            raise HTTPException(404, "Unknown action")
    except rv.ReviewError as e:
        raise _bad(e) from e
    hooks.emit_state()
    return {"ok": True}


@router.post("/tasks/{iid}/resend")
def resend(iid: str) -> dict:
    try:
        return agents.resend(iid)
    except agents.SendError as e:
        raise _bad(e) from e


@router.post("/tasks/{iid}/works")
def task_works(iid: str) -> dict:
    try:
        agents.mark_works(iid)
    except agents.SendError as e:
        raise _bad(e) from e
    return {"ok": True}


@router.post("/tasks/{iid}/still-broken")
def task_still_broken(iid: str) -> dict:
    try:
        return {"draft_id": agents.still_broken(iid)}
    except agents.SendError as e:
        raise _bad(e) from e


class AnswerIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/tasks/{iid}/answer")
def task_answer(iid: str, body: AnswerIn) -> dict:
    try:
        agents.answer(iid, body.text)
    except agents.SendError as e:
        raise _bad(e) from e
    return {"ok": True}


@router.post("/tasks/{iid}/unmerge")
def unmerge(iid: str) -> dict:
    agents.dismiss_duplicate(iid)
    return {"ok": True}


@router.post("/runs/{rid}/stop")
def stop_run(rid: str) -> dict:
    with read_session() as s:
        if s.get(AgentRun, rid) is None:
            raise HTTPException(404, "Run not found")
    core().agents.cancel(rid)
    return {"ok": True}


@router.post("/runs/{rid}/open-log")
def open_log(rid: str) -> dict:
    import os

    with read_session() as s:
        r = s.get(AgentRun, rid)
        path = r.log_path if r else None
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "This run has no log yet.")
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - Checkpoint's own log file
    return {"path": path}


# ------------------------------------------------------------------ preview, checks and the branch
def _session_project(sid: str) -> tuple[Session, Project]:
    with read_session() as s:
        sess = s.get(Session, sid)
        if sess is None:
            raise HTTPException(404, "Session not found")
        return sess, s.get(Project, sess.project_id)


@router.post("/sessions/{sid}/preview/{action}")
def preview(sid: str, action: str) -> dict:
    sess, proj = _session_project(sid)
    pm = core().previews
    if action == "stop":
        pm.stop(sid)
        return pm.status(sid)
    if action != "restart":
        raise HTTPException(404, "Unknown action")
    try:
        agents.restart_preview(sid, pm)
    except agents.SendError as e:
        raise _bad(e, 409) from e
    return pm.status(sid)


@router.post("/sessions/{sid}/open-folder")
def open_folder(sid: str) -> dict:
    import os

    sess, _ = _session_project(sid)
    if not sess.worktree_path:
        raise HTTPException(409, "No working copy yet")
    if os.name == "nt":
        os.startfile(sess.worktree_path)  # noqa: S606 - the session's own working copy
    return {"ok": True}


@router.post("/sessions/{sid}/pull-request")
def pull_request(sid: str) -> dict:
    """Push the session branch and open a pull request (with gh), or the host's compare page. Never merges."""
    sess, proj = _session_project(sid)
    if not sess.branch or not sess.worktree_path:
        raise HTTPException(409, "Nothing has been sent from this session yet.")
    try:
        if ws.dirty_files(sess.worktree_path):
            raise HTTPException(409, "The working copy has uncommitted changes. Commit or discard them first.")
        ws.git(["push", "-u", "origin", sess.branch], sess.worktree_path, timeout=180)
        base = proj.base_branch or ws.inspect_repo(proj.repo_path).default_branch
        url = None
        if ws.gh_available():
            try:
                url = ws.run_gh(["pr", "view", sess.branch, "--json", "url", "-q", ".url"], sess.worktree_path)
            except ws.GitError:
                url = ws.run_gh(["pr", "create", "--base", base, "--head", sess.branch, "--title", sess.title,
                                 "--body", _pr_body(sid)], sess.worktree_path).splitlines()[-1]
        else:
            web = ws.web_url(ws.git(["remote", "get-url", "origin"], sess.worktree_path))
            if not web:
                raise HTTPException(409, "Pushed, but the remote isn't a web host Checkpoint knows how to open.")
            url = f"{web}/compare/{base}...{sess.branch}?expand=1"
    except ws.GitError as e:
        raise _bad(e, 409) from e
    with write_session() as s:
        s.get(Session, sid).pr_url = url
    webbrowser.open(url)
    return {"url": url}


def _pr_body(sid: str) -> str:
    st = agents.session_flow(sid)
    lines = [f"From the Checkpoint session “{st['session']['title']}”.", ""]
    for t in st["tasks"]:
        if t["code"]:
            lines.append(f"- {t['code']} {t['title']} ({t['label']})")
    return "\n".join(lines)


@router.post("/sessions/{sid}/merge")
def merge_pr(sid: str) -> dict:
    """Merge the session's pull request. Only ever runs when the user presses Merge."""
    sess, _ = _session_project(sid)
    if not sess.branch or not sess.worktree_path:
        raise HTTPException(409, "Nothing has been sent from this session yet.")
    if not ws.gh_available():
        if sess.pr_url:
            webbrowser.open(sess.pr_url)
            return {"merged": False, "url": sess.pr_url, "message": "Merge it on the page that just opened."}
        raise HTTPException(409, "Open a pull request first.")
    try:
        ws.run_gh(["pr", "merge", sess.branch, "--merge"], sess.worktree_path, timeout=300)
    except ws.GitError as e:
        raise _bad(e, 409) from e
    from ..models import WorkItem, utcnow

    with write_session() as s:  # merged: what you saw working is done
        for w in s.query(WorkItem).filter(WorkItem.session_id == sid, WorkItem.agent_state == "ready").all():
            w.agent_state, w.work_status, w.completed_at = "done", "done", utcnow()
    return {"merged": True}


@router.post("/sessions/{sid}/clean-up")
def clean_up(sid: str) -> dict:
    """Stop the preview and remove the session's working copy. The branch (and its commits) stay."""
    sess, proj = _session_project(sid)
    if not sess.worktree_path:
        return {"ok": True}
    with read_session() as s:
        busy = s.query(AgentRun).filter(AgentRun.session_id == sid, AgentRun.state.in_(("queued", "starting", "running", "checking"))).first()
    if busy:
        raise HTTPException(409, "An agent is still working in this session.")
    if ws.dirty_files(sess.worktree_path):
        raise HTTPException(409, "The working copy has uncommitted changes, so it wasn't removed.")
    core().previews.stop(sid)
    ws.remove_worktree(proj.repo_path, sess.worktree_path)
    with write_session() as s:
        s.get(Session, sid).worktree_path = None
    return {"ok": True}
