"""Shift+F9: switch project (ending the running session) or set up a new one from the last project's settings."""

import os
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _project(name, **settings):
    from checkpoint.db import write_session
    from checkpoint.models import Project

    with write_session() as s:
        p = Project(name=name, **settings)
        s.add(p)
        s.flush()
        return p.id


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True)
    return path


@pytest.fixture()
def env(app_env, monkeypatch, tmp_path):
    from checkpoint.services.sessions import SourceConfig, StartSessionRequest
    from checkpoint.services.switcher_model import SwitcherModel
    from test_quick_start import _manager

    sm = _manager(monkeypatch)
    old = _repo(tmp_path / "games" / "dodge-this")
    _repo(tmp_path / "games" / "space-crab")
    a = _project("Dodge This", repo_path=str(old), base_branch="trunk", default_agent="claude", agent_access="full",
                 setup_command="npm install", check_command="npm test", preview_command="npm run dev",
                 preview_url="http://localhost:5173")
    b = _project("Bolt")
    c = _project("Never Used")
    sm.start(StartSessionRequest(project_id=b, mic=SourceConfig(enabled=True)))
    sm.end()
    sm.start(StartSessionRequest(project_id=a, mic=SourceConfig(enabled=True), loopback=SourceConfig(enabled=True)))
    switched = []
    m = SwitcherModel(sessions=sm, on_switched=lambda pid, sid, ended: switched.append((pid, ended)))
    return m, sm, {"a": a, "b": b, "c": c}, switched, tmp_path


def test_list_puts_running_first_and_picks_somewhere_else(env):
    m, _sm, ids, _sw, _ = env
    m.open()
    assert [r["name"] for r in m.rows] == ["Dodge This", "Bolt", "Never Used"]
    assert m.rows[0]["running"] and m.running_name == "Dodge This"
    assert m.selected["name"] == "Bolt"  # the running project is rarely where you want to go
    m.set_query("ne")
    assert [r["name"] for r in m.filtered] == ["Never Used"]  # prefix matches first
    m.set_query("zzz")
    assert m.on_new_row


def test_switching_ends_the_running_session_and_starts_there(env):
    m, sm, ids, switched, _ = env
    first = sm.active.id
    m.open()
    msg = m.choose()  # Bolt
    assert msg == "Recording in Bolt · ended the Dodge This session"
    assert sm.active.project_id == ids["b"] and sm.active.id != first
    assert switched == [(ids["b"], first)]
    assert set(sm.active.recorders) == {"mic"}  # Bolt's own remembered setup
    m.open()
    m.index = 0  # Bolt, recording now
    assert m.choose() is None and "Already recording" in m.error


def test_new_project_copies_the_last_project_and_finds_its_repo(env):
    m, sm, ids, switched, tmp = env
    m.open({"exe": "SpaceCrab.exe", "title": "Space Crab"})
    m.set_query("Space Crab")
    assert m.on_new_row
    m.choose()
    assert m.mode == "new" and m.template["name"] == "Dodge This"
    d = m.draft
    assert d["name"] == "Space Crab" and d["default_agent"] == "claude" and d["agent_access"] == "full"
    assert (d["setup_command"], d["check_command"], d["preview_command"]) == ("npm install", "npm test", "npm run dev")
    assert d["mic"] and d["loopback"]
    assert d["program"] == "SpaceCrab.exe" and d["program_kind"] == "exe"
    assert d["repo_path"] == str(tmp / "games" / "space-crab")  # next to the last project's repo
    assert m.repo_status["ok"] and m.repo_status["branch"] == "trunk"

    m.set_field("loopback", False)
    msg = m.create()
    assert msg and msg.startswith("Created Space Crab · Recording in Space Crab")
    from checkpoint.db import read_session
    from checkpoint.models import Project
    from checkpoint.settings_store import get_settings

    pid = sm.active.project_id
    with read_session() as s:
        p = s.get(Project, pid)
        assert p.name == "Space Crab" and p.base_branch == "trunk" and p.default_agent == "claude"
        assert p.repo_path == str(tmp / "games" / "space-crab")
    assert set(sm.active.recorders) == {"mic"}
    assert any(r.project_id == pid and r.match == "SpaceCrab.exe" for r in get_settings().app_watch.rules)
    assert m.mode == "list" and m.draft == {}


def test_new_project_keeps_the_draft_and_checks_names(env):
    m, _sm, _ids, _sw, _ = env
    m.open({"exe": "chrome.exe", "title": "Docs"})
    m.start_new()
    assert m.draft["program"] == ""  # a browser isn't what you're testing
    assert "No repo folder" in m.repo_status["text"]
    m.set_field("name", "bolt")
    assert m.create() is None and "already a project" in m.error
    m.set_field("name", "Thing")
    m.set_field("repo_path", "Z:/nowhere/at/all")
    assert m.repo_status["ok"] is False
    assert m.create() is None
    m.back()
    m.open()  # Esc and Shift+F9 again: the half-filled project is still there
    m.start_new()
    assert m.mode == "new" and m.draft["name"] == "Thing"


def test_switcher_widget_keys(env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from checkpoint.desktop.switcher import ProjectSwitcher

    m, sm, ids, _sw, _ = env
    app = QApplication.instance() or QApplication([])
    done = []
    sw = ProjectSwitcher(sm, lambda msg, work: done.append(msg))
    sw.model.run_async = m.run_async  # inline in tests
    sw.toggle(None, None, 0)
    assert sw.isVisible() and sw.search.isVisible() and not sw.form.isVisible()
    sw.grab()  # paints without errors
    QTest.keyClicks(sw.search, "nev")
    assert sw.model.selected["name"] == "Never Used"
    QTest.keyClick(sw.search, Qt.Key.Key_Down)
    assert sw.model.on_new_row
    QTest.keyClick(sw.search, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert sw.form.isVisible() and sw.boxes["name"].text() == "nev"
    sw.grab()
    QTest.keyClick(sw.boxes["name"], Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert sw.model.mode == "list" and sw.search.text() == "nev"
    QTest.keyClick(sw.search, Qt.Key.Key_Up)
    QTest.keyClick(sw.search, Qt.Key.Key_Return)
    assert done and sm.active.project_id == ids["c"] and not sw.isVisible()
    sw.close()
    del app
