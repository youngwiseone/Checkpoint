"""One-key sessions: program detection, remembered setup and live source changes."""


class FakeRecorder:
    def __init__(self, source_id, kind, device, label):
        self.source_id, self.kind, self.device, self.label = source_id, kind, device, label
        self.state, self.paused, self.stopped = "recording", False, False

    def start(self):
        pass

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self, timeout=5.0):
        self.stopped = True

    def status(self):
        return {"source_id": self.source_id, "kind": self.kind, "label": self.label, "state": self.state}


def _manager(monkeypatch):
    from checkpoint.services.sessions import SessionManager

    sm = SessionManager()
    monkeypatch.setattr(sm, "_make_recorder", lambda a, sid, kind, dev, label: FakeRecorder(sid, kind, dev, label))
    return sm


def _project(name="Dodge This"):
    from checkpoint.db import write_session
    from checkpoint.models import Project

    with write_session() as s:
        p = Project(name=name)
        s.add(p)
        s.flush()
        return p.id


def test_rules_match_exe_or_title():
    from checkpoint.services.appwatch import rule_matches
    from checkpoint.settings_store import WatchRule

    wins = [{"exe": "Discord.exe", "title": "#game-updates | Dodge This - Discord"},
            {"exe": "Godot_v4.3.exe", "title": "Dodge This! (DEBUG)"}]
    assert rule_matches(WatchRule(project_id="p", kind="exe", match="godot_v4.3"), wins)["title"] == "Dodge This! (DEBUG)"
    assert rule_matches(WatchRule(project_id="p", kind="exe", match="Dodge This"), wins) is None  # exe rules ignore titles
    assert rule_matches(WatchRule(project_id="p", kind="title", match="dodge this! (debug)"), wins)["exe"] == "Godot_v4.3.exe"


def test_offer_when_program_starts_and_quick_start_uses_it(app_env, monkeypatch):
    from checkpoint.events import hooks
    from checkpoint.services.appwatch import AppWatcher
    from checkpoint.settings_store import update_settings

    pid = _project()
    other = _project("Other")
    update_settings({"last_project_id": other, "app_watch": {"rules": [{"project_id": pid, "kind": "exe", "match": "DodgeThis.exe"}]}})
    sm = _manager(monkeypatch)
    windows = []
    w = AppWatcher(sm, lambda: windows)
    offers = []
    monkeypatch.setattr(hooks, "offer_session", offers.append)
    w.step()
    assert w.offer is None
    windows.append({"exe": "DodgeThis.exe", "title": "Dodge This"})
    w.step()
    w.step()  # still running: offered once, not every tick
    assert len(offers) == 1 and offers[0]["project_name"] == "Dodge This"

    import checkpoint.core as core_mod

    c = core_mod.AppCore.__new__(core_mod.AppCore)
    c.sessions, c.appwatch = sm, w
    c.quick_start()
    assert sm.active.project_id == pid and w.offer is None
    sm.end()
    windows.clear()
    w.step()
    assert w.running == set()


def test_remembered_setup_follows_last_session(app_env, monkeypatch):
    from checkpoint.services.sessions import SourceConfig, StartSessionRequest, remembered_setup
    from checkpoint.settings_store import update_settings

    update_settings({"audio": {"mic_enabled": True, "mic_label": "Me"}})
    pid = _project()
    fresh = remembered_setup(pid)  # no sessions yet: saved defaults
    assert fresh.mic.enabled and not fresh.loopback.enabled

    sm = _manager(monkeypatch)
    sm.start(StartSessionRequest(project_id=pid, mic=SourceConfig(enabled=True, label="Bligh"),
                                 loopback=SourceConfig(enabled=True, device="Speakers", label="Discord"),
                                 transcription_mode="live", always_ask_context=True))
    sm.set_source("mic", False)  # turned off mid-session: stays off next time
    sm.end()
    r = remembered_setup(pid)
    assert not r.mic.enabled
    assert r.loopback.enabled and r.loopback.device == "Speakers" and r.loopback.label == "Discord"
    assert r.transcription_mode == "live" and r.always_ask_context
    assert remembered_setup(_project("New")).loopback.label == "Discord"  # new project: like the last session anywhere


def test_sources_can_be_switched_live(app_env, monkeypatch):
    from sqlalchemy import select

    from checkpoint.db import read_session
    from checkpoint.models import AudioEvent, AudioSource, Session
    from checkpoint.services.sessions import StartSessionRequest

    sm = _manager(monkeypatch)
    sid = sm.start(StartSessionRequest(project_id=_project()))  # no audio: transcription off
    assert sm.status()["sources"] == []
    sm.set_source("mic", True)
    assert [s["kind"] for s in sm.status()["sources"]] == ["mic"]
    assert sm.active.transcription_mode in ("after", "live")
    sm.pause()
    sm.set_source("loopback", True)
    assert sm.active.recorders["loopback"].paused  # a source added while paused doesn't record
    sm.resume()
    mic = sm.active.recorders["mic"]
    sm.set_source("mic", False)
    assert mic.stopped and "mic" not in sm.active.recorders
    sm.set_source("mic", True)
    assert sm.active.recorders["mic"].source_id == mic.source_id  # same source row, same folder
    sm.set_options(always_ask_context=True)
    with read_session() as s:
        assert s.get(Session, sid).always_ask_context
        kinds = s.scalars(select(AudioEvent.kind).where(AudioEvent.source_id == mic.source_id).order_by(AudioEvent.offset_ms)).all()
        assert kinds == ["disabled", "enabled"]
        assert len(s.scalars(select(AudioSource).where(AudioSource.session_id == sid)).all()) == 2
    sm.end()


def test_task_text_asks_to_work_through_batches():
    from checkpoint.services.handoff import task_text

    assert "one at a time" in task_text("implement_commit", types={"bug"}, count=5)
    assert "one at a time" not in task_text("implement_commit", types={"bug"}, count=1)
    assert task_text("read", count=5) == ""
