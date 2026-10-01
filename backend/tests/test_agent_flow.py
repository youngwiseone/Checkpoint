"""Capture → review → send → ready: naming, suggestions, the overlay's keys, sending to an agent."""

import subprocess
import sys
import textwrap

import pytest


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "game"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Tester")
    (r / "README.md").write_text("game\n")
    _git(r, "add", ".")
    _git(r, "commit", "-m", "start")
    return r


FAKE_AGENT = textwrap.dedent('''
    import json, re, subprocess, sys
    prompt = sys.stdin.read()
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "agent-session-1"}), flush=True)
    for code in re.findall(r"^### (T-\\d+)", prompt, re.M):
        open(f"{code}.txt", "w").write("fixed\\n")
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=Agent", "commit", "-m", f"{code}: fix it"], check=True,
                       capture_output=True)
    print(json.dumps({"type": "result", "result": "All done", "is_error": False}), flush=True)
''')


def _project(repo=None, **kw):
    from checkpoint.db import write_session
    from checkpoint.models import Project

    with write_session() as s:
        p = Project(name="Dodge This", repo_path=str(repo) if repo else None, default_agent="claude" if repo else "none",
                    check_command=kw.get("check", "git status"), base_branch="main")
        s.add(p)
        s.flush()
        return p.id


def _session(pid):
    from checkpoint.db import write_session
    from checkpoint.models import Session

    with write_session() as s:
        x = Session(project_id=pid, title="Session Wed 01 Oct 2026, 09:00", state="ended", transcription_mode="off")
        s.add(x)
        s.flush()
        return x.id


def _card(pid, sid, title, desc=""):
    from checkpoint.services import review as rv

    return rv.create_draft(pid, sid, "bug", title, desc)["id"]


# ------------------------------------------------------------------ naming
def test_names_are_suggested_then_branch_is_git_safe():
    from checkpoint.services import naming

    assert naming.suggest_name(["Fix water deaths after players sink"]) == "Water deaths after players sink"
    name = naming.suggest_name(["Water deaths after sinking", "The kill cam should be clearer", "Replay is pixelated"])
    assert name == "Water deaths after sinking and kill cam clearer (+1)"
    assert naming.slug("Kill cam: “clearer” / Ärger!") == "kill-cam-clearer-arger"
    taken = {"checkpoint/kill-cam", "checkpoint/kill-cam-2"}
    assert naming.branch_for("Kill cam", taken.__contains__) == "checkpoint/kill-cam-3"
    assert naming.branch_for("Replay", taken.__contains__) == "checkpoint/replay"
    assert naming.is_default_title("Session Wed 01 Oct 2026, 09:00")


def test_name_refines_while_reviewing_and_locks_on_send(app_env, repo, monkeypatch):
    from checkpoint.db import read_session
    from checkpoint.models import Session
    from checkpoint.services import agents, flow

    monkeypatch.setattr(agents, "find_agent", lambda a: sys.executable)
    pid = _project(repo)
    sid = _session(pid)
    a = _card(pid, sid, "Water deaths after sinking")
    _card(pid, sid, "Kill cam should be clearer")
    flow.approve(a)
    with read_session() as s:
        assert s.get(Session, sid).title == "Water deaths after sinking and kill cam clearer"
    agents.rename(sid, "Water and kill cam")
    flow.dismiss(_card(pid, sid, "Something else entirely"))
    with read_session() as s:
        assert s.get(Session, sid).title == "Water and kill cam"  # an edited name isn't overwritten
    res = agents.send(sid)
    assert res["branch"] == "checkpoint/water-and-kill-cam" and res["count"] == 1
    with pytest.raises(agents.SendError):
        agents.rename(sid, "Something new")


# ------------------------------------------------------------------ background captures become suggestions
def test_marker_with_speech_becomes_a_suggestion(app_env):
    from datetime import datetime, timezone

    from checkpoint.db import read_session, write_session
    from checkpoint.models import AudioSource, DraftItem, TranscriptSegment
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.suggestions import promote
    from conftest import png_bytes

    pid = _project()
    sid = _session(pid)
    png, thumb = png_bytes()
    with write_session() as s:
        src = AudioSource(session_id=sid, kind="mic", label="Me")
        s.add(src)
        s.flush()
        src_id = src.id
    cid = CaptureService(None).import_image(png, thumb, 320, 180, project_id=pid, session_id=sid, offset_ms=60_000,
                                            taken_at=datetime.now(timezone.utc), status="marker", trigger="hotkey")
    with write_session() as s:
        s.add(TranscriptSegment(id="g1", session_id=sid, source_id=src_id, start_ms=55_000, end_ms=59_000,
                                text="The player gets stuck in the floor here."))
        s.add(TranscriptSegment(id="g2", session_id=sid, source_id=src_id, start_ms=80_000, end_ms=82_000, text="Moving on."))
    assert promote(sid) == 1
    assert promote(sid) == 0  # never twice
    with read_session() as s:
        d = s.query(DraftItem).filter_by(session_id=sid).one()
        assert d.origin == "suggestion" and d.type == "bug"
        assert d.title == "The player gets stuck in the floor here."
    del cid


# ------------------------------------------------------------------ the overlay
def test_overlay_keys_approve_dismiss_undo_edit(app_env):
    from checkpoint.db import read_session
    from checkpoint.models import DraftItem
    from checkpoint.services.overlay_model import OverlayModel

    pid = _project()
    sid = _session(pid)
    ids = [_card(pid, sid, t) for t in ("First problem here", "Second problem here", "Third problem here")]
    m = OverlayModel()
    m.open(sid)
    assert m.card["id"] == ids[0] and m.position == (1, 3)
    assert m.key("a")  # approve and advance
    assert m.card["id"] == ids[1] and m.approved_unsent == 1 and m.position == (2, 3)
    assert m.key("d")  # dismiss and advance, with undo
    assert m.card["id"] == ids[2] and "U to undo" in m.message
    assert m.key("u")
    assert m.card["id"] == ids[1]
    with read_session() as s:
        assert s.get(DraftItem, ids[1]).review_state == "pending"

    assert m.key("e") and m.mode == "edit"
    assert not m.key("a")  # letters are text while typing
    m.typed("Second problem, clearer\n\nSteps: jump twice")
    m.open(sid)  # Esc hides; reopening keeps the unsaved text and the mode
    assert m.mode == "edit" and m.edit_text().startswith("Second problem, clearer")
    m.save_edit()
    assert m.mode == "review" and m.card["title"] == "Second problem, clearer"
    assert m.card["description"] == "Steps: jump twice"

    m.key("u")  # undoes the first approval: back to pending, nothing was sent
    with read_session() as s:
        assert s.get(DraftItem, ids[0]).review_state == "pending"


def test_overlay_asks_only_when_the_project_is_unclear(app_env):
    from checkpoint.services import flow

    a, b = _project(), _project()
    sa, sb = _session(a), _session(b)
    _card(a, sa, "Problem in A")
    assert flow.target(None) == {"session_id": sa}
    _card(b, sb, "Problem in B")
    assert len(flow.target(None)["choices"]) == 2
    assert flow.target("active-one") == {"session_id": "active-one"}


# ------------------------------------------------------------------ sending
def _fake_agent(monkeypatch, tmp_path, script=FAKE_AGENT):
    from checkpoint.services import agents

    fake = tmp_path / "fake_agent.py"
    fake.write_text(script)
    seen = {}

    def cmd(agent, exe, access, cwd, run_id, resume_id, check=""):
        seen.setdefault("resume", []).append(resume_id)
        return [sys.executable, str(fake)], None

    monkeypatch.setattr(agents, "find_agent", lambda a: sys.executable)
    monkeypatch.setattr(agents, "agent_command", cmd)
    return seen


def _run_queued(worker):
    from checkpoint.db import read_session
    from checkpoint.models import AgentRun

    with read_session() as s:
        ids = [r.id for r in s.query(AgentRun).filter_by(state="queued").order_by(AgentRun.created_at).all()]
    for rid in ids:
        worker._execute_safe(rid)


def test_send_runs_agent_on_session_branch_and_marks_ready(app_env, repo, tmp_path, monkeypatch):
    from checkpoint.db import read_session
    from checkpoint.models import AgentRun, Session, WorkItem
    from checkpoint.services import agents, flow
    from checkpoint.services.preview import PreviewManager

    seen = _fake_agent(monkeypatch, tmp_path)
    pid = _project(repo)
    sid = _session(pid)
    a, b = _card(pid, sid, "Water deaths after sinking"), _card(pid, sid, "Kill cam unclear on death")
    _card(pid, sid, "Unreviewed card stays out")
    flow.approve(a)
    flow.approve(b)
    plan = agents.plan(sid)
    assert plan["count"] == 2 and plan["branch"].startswith("checkpoint/") and plan["branch_new"] and not plan["problems"]
    res = agents.send(sid)
    with pytest.raises(agents.SendError):
        agents.send(sid)  # nothing left to send: no duplicate sends
    with read_session() as s:
        assert {w.agent_state for w in s.query(WorkItem).filter_by(session_id=sid)} == {"sending"}  # not sent until accepted

    worker = agents.AgentWorker(PreviewManager())
    _run_queued(worker)
    with read_session() as s:
        sess = s.get(Session, sid)
        items = s.query(WorkItem).filter_by(session_id=sid).order_by(WorkItem.task_number).all()
        run = s.get(AgentRun, res["run_id"])
        assert run.state == "done" and run.summary == "All done" and run.accepted_at is not None
        assert [w.agent_state for w in items] == ["ready", "ready"]
        assert all(w.sent_at and w.agent_commit for w in items)
        assert sess.agent_session_id == "agent-session-1"
        wt = sess.worktree_path
    assert _git(wt, "branch", "--show-current") == res["branch"]
    assert _git(repo, "branch", "--show-current") == "main"  # the user's checkout is untouched
    assert "T-2: fix it" in _git(wt, "log", "--format=%s")
    assert not (repo / "T-1.txt").exists()

    # A later batch in the same session lands on the same branch and resumes the agent's conversation.
    flow.approve(_card(pid, sid, "Replay looks pixelated"))
    res2 = agents.send(sid)
    assert res2["branch"] == res["branch"]
    _run_queued(worker)
    assert seen["resume"] == [None, "agent-session-1"]
    assert "T-3: fix it" in _git(wt, "log", "--format=%s")
    flow_state = agents.session_flow(sid)
    assert [t["code"] for t in flow_state["tasks"] if t["code"]] == ["T-1", "T-2", "T-3"]
    assert flow_state["ready_to_refresh"]  # no preview command: Ready means checked and committed


def test_failed_checks_and_silent_agent_need_you(app_env, repo, tmp_path, monkeypatch):
    from checkpoint.db import read_session
    from checkpoint.models import WorkItem
    from checkpoint.services import agents, flow
    from checkpoint.services.preview import PreviewManager

    _fake_agent(monkeypatch, tmp_path)
    pid = _project(repo, check=f'"{sys.executable}" -c "raise SystemExit(3)"')
    sid = _session(pid)
    flow.approve(_card(pid, sid, "Water deaths after sinking"))
    agents.send(sid)
    _run_queued(agents.AgentWorker(PreviewManager()))
    with read_session() as s:
        w = s.query(WorkItem).filter_by(session_id=sid).one()
        assert w.agent_state == "needs_you" and "checks failed" in w.agent_note

    quiet = 'import json,sys; sys.stdin.read(); print(json.dumps({"type":"system","session_id":"x"})); print(json.dumps({"type":"result","result":"Hmm"}))'
    _fake_agent(monkeypatch, tmp_path, quiet)
    agents.resend(w.id)
    agents.send(sid)
    _run_queued(agents.AgentWorker(PreviewManager()))
    with read_session() as s:
        w = s.query(WorkItem).filter_by(session_id=sid).one()
        assert w.agent_state == "needs_you" and "without committing" in w.agent_note


def test_repeat_report_adds_evidence_instead_of_a_second_fix(app_env, repo, monkeypatch):
    from checkpoint.db import read_session, write_session
    from checkpoint.models import WorkItem
    from checkpoint.services import agents, flow

    monkeypatch.setattr(agents, "find_agent", lambda a: sys.executable)
    pid = _project(repo)
    sid = _session(pid)
    flow.approve(_card(pid, sid, "Players die in shallow water"))
    agents.send(sid)
    flow.approve(_card(pid, sid, "Players die in shallow water again"))
    plan = agents.plan(sid)
    assert plan["count"] == 0 and plan["merges"][0]["into"] == "T-1"
    res = agents.send(sid)
    assert res == {"count": 0, "merged": 1, "run_id": None, "branch": res["branch"]}
    with read_session() as s:
        first = s.query(WorkItem).filter_by(task_number=1).one()
        assert "Reported again" in first.agent_note
    # Once T-1 is finished, a repeat report is sent again as a follow-up instead.
    with write_session() as s:
        s.query(WorkItem).filter_by(task_number=1).one().agent_state = "ready"
    flow.approve(_card(pid, sid, "Players still die in shallow water"))
    plan = agents.plan(sid)
    assert plan["count"] == 1 and plan["tasks"][0]["follow_up_of"] == "T-1"


def test_agent_reports_progress_through_the_connector(app_env, repo, monkeypatch):
    from checkpoint.db import read_session
    from checkpoint.models import WorkItem
    from checkpoint.services import agents, flow

    monkeypatch.setattr(agents, "find_agent", lambda a: sys.executable)
    pid = _project(repo)
    sid = _session(pid)
    flow.approve(_card(pid, sid, "Water deaths after sinking"))
    run_id = agents.send(sid)["run_id"]
    assert "Recorded" in agents.report(run_id, "T-1", "working")
    with read_session() as s:
        assert s.query(WorkItem).one().agent_state == "working"
    assert "Recorded" in agents.report(run_id, "T-1", "blocked", "Should water kill at all?")
    with read_session() as s:
        w = s.query(WorkItem).one()
        assert w.agent_state == "needs_you" and w.agent_note == "Should water kill at all?"
    assert "ignored" in agents.report(run_id, "T-1", "done")  # finished tasks can't be moved by the agent
    assert "no task T-9" in agents.report(run_id, "T-9", "done")


def test_send_needs_project_setup(app_env):
    from checkpoint.services import agents, flow

    pid = _project()
    sid = _session(pid)
    flow.approve(_card(pid, sid, "Something to fix"))
    plan = agents.plan(sid)
    assert plan["problems"] and "repository" in plan["problems"][0]
    with pytest.raises(agents.SendError):
        agents.send(sid)


# ------------------------------------------------------------------ flow tweaks
def test_working_copy_is_prepared_before_the_first_send(app_env, repo, tmp_path, monkeypatch):
    from checkpoint.db import read_session
    from checkpoint.models import Session
    from checkpoint.services import agents, flow
    from checkpoint.services.preview import PreviewManager

    _fake_agent(monkeypatch, tmp_path)
    pid = _project(repo)
    sid = _session(pid)
    flow.approve(_card(pid, sid, "Water deaths after sinking"))
    worker = agents.AgentWorker(PreviewManager())
    assert worker._prepare_some(set())
    worker._threads[sid].join(30)
    with read_session() as s:
        wt = s.get(Session, sid).worktree_path
    assert wt and _git(wt, "branch", "--show-current").startswith("checkpoint/prep-")
    (repo / "later.txt").write_text("base moved on\n")  # the base branch moves while you review
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "later")
    res = agents.send(sid)
    _run_queued(worker)
    assert _git(wt, "branch", "--show-current") == res["branch"]  # renamed, same folder
    assert (pathlib_path(wt) / "later.txt").exists()  # caught up before the agent started
    assert "T-1: fix it" in _git(wt, "log", "--format=%s")


def pathlib_path(p):
    from pathlib import Path

    return Path(p)


def test_trying_the_result_closes_the_loop(app_env, repo, monkeypatch):
    from checkpoint.db import read_session, write_session
    from checkpoint.models import DraftItem, WorkItem
    from checkpoint.services import agents, flow
    from checkpoint.services.overlay_model import OverlayModel

    monkeypatch.setattr(agents, "find_agent", lambda a: sys.executable)
    pid = _project(repo)
    sid = _session(pid)
    flow.approve(_card(pid, sid, "Water deaths after sinking"))
    flow.approve(_card(pid, sid, "Kill cam unclear on death"))
    agents.send(sid)
    with write_session() as s:
        t1, t2 = s.query(WorkItem).order_by(WorkItem.task_number).all()
        t1.agent_state, t2.agent_state, t2.agent_note = "ready", "needs_you", "Label it Replay or Kill cam?"
        t1_id, t2_id = t1.id, t2.id

    m = OverlayModel()
    m.open(sid)
    m.key("tab")
    assert m.mode == "tasks" and m.task_id == t1_id
    m.key("y")  # it works
    with read_session() as s:
        assert s.get(WorkItem, t1_id).work_status == "done"
    assert m.task_id == t2_id
    m.key("e")
    assert m.mode == "answer" and not m.key("a")  # typing, not acting
    m.typed("Call it Replay")
    m.save_answer()
    with read_session() as s:
        w = s.get(WorkItem, t2_id)
        assert w.agent_state is None and "Call it Replay" in w.description and "Replay or Kill cam?" in w.description
    agents.send(sid)
    with read_session() as s:
        assert s.get(WorkItem, t2_id).task_number == 2  # keeps its T-number when sent again

    # Still broken after a fix: a follow-up card, in edit mode, matched to the task when approved.
    with write_session() as s:
        s.get(WorkItem, t2_id).agent_state = "ready"
    m.refresh()
    m.mode, m.task_id = "tasks", t2_id
    m.key("f")
    assert m.mode == "edit" and m.card["title"].startswith("Still broken: Kill cam")
    m.typed("Still broken: Kill cam unclear on death\n\nNo label at all now")
    m.save_edit()
    m.key("a")
    plan = agents.plan(sid)
    assert plan["tasks"][0]["follow_up_of"] == "T-2"
    assert m.mode == "confirm"  # the last card went straight to the send step
    with read_session() as s:
        assert s.query(DraftItem).filter_by(review_state="pending").count() == 0


def test_agent_activity_lines():
    from checkpoint.services.agents import activity

    ev = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "C:/g/water.go"}}]}}
    assert activity(ev) == "Editing water.go"
    ev = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Task", "input": {"description": "Fix kill cam"}}]}}
    assert activity(ev) == "Subagent: Fix kill cam"
    assert activity({"type": "item.started", "item": {"type": "command_execution", "command": "go test ./..."}}) == "Running go test ./..."
    assert activity({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}) is None
