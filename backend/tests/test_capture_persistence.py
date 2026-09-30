"""Manual capture → card → approve → done survives a restart; crash recovery repairs audio."""

from datetime import datetime, timezone

from conftest import png_bytes, restart


def test_typed_capture_to_done_survives_restart(app_env):
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager, StartSessionRequest
    from checkpoint.services import review as rv
    from checkpoint.db import write_session
    from checkpoint.models import Project
    from checkpoint.storage import media_path

    with write_session() as s:
        p = Project(name="Report")
        s.add(p)
        s.flush()
        pid = p.id
    sm = SessionManager()
    cs = CaptureService(sm)
    sid = sm.start(StartSessionRequest(project_id=pid))  # both audio toggles off
    assert sm.audio_functioning() is False  # → F8 must ask for a note

    png, thumb = png_bytes()
    cid = cs.import_image(png, thumb, 320, 180, project_id=pid, session_id=sid, offset_ms=sm.active.clock.offset_ms(),
                          taken_at=datetime.now(timezone.utc), status="unfinished", trigger="hotkey")
    try:
        cs.save_capture_note(cid, "   ", None)
        raise AssertionError("blank note must be rejected")
    except ValueError:
        pass
    res = cs.save_capture_note(cid, "Use the latest loaded date instead of today's date for this range\nmore detail", None)
    d = rv.get_draft(res["draft_id"])
    assert d["title"] == "Use the latest loaded date instead of today's date for this range"
    assert "more detail" in d["description"]
    assert d["type"] in ("improvement", "task")
    assert {e["kind"] for e in d["evidence"]} == {"capture", "note"}

    q = cs.quick_note("Remember to check the reset-filters button")
    assert rv.get_draft(q["draft_id"])["type"] == "task"

    rv.update_draft(res["draft_id"], {"title": "Use latest loaded date for range"})
    a1 = rv.approve([res["draft_id"]])
    a2 = rv.approve([res["draft_id"]])  # idempotent
    assert a1[0]["work_item_id"] == a2[0]["work_item_id"]
    wid = a1[0]["work_item_id"]
    assert len(rv.list_work_items(pid)) == 1
    rv.update_work_item(wid, {"work_status": "done"})
    sm.end()

    restart()
    item = rv.get_work_item(wid)
    assert item["work_status"] == "done" and item["title"] == "Use latest loaded date for range"
    caps = [e for e in item["evidence"] if e["kind"] == "capture"]
    assert len(caps) == 1
    from checkpoint.db import read_session
    from checkpoint.models import Capture

    with read_session() as s:
        c = s.get(Capture, caps[0]["capture_id"])
        assert media_path(c.image_rel_path).read_bytes() == png
    assert rv.review_counts(pid)["pending"] == 1  # the F9 note is still waiting


def test_escape_keeps_unfinished_and_discard_removes(app_env):
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager
    from checkpoint.services import review as rv
    from checkpoint.db import read_session
    from checkpoint.models import Capture, DraftItem

    sm = SessionManager()
    cs = CaptureService(sm)
    from checkpoint.services.capture import default_project_id

    pid = default_project_id()
    png, thumb = png_bytes()
    cid = cs.import_image(png, thumb, 10, 10, project_id=pid, session_id=None, offset_ms=None,
                          taken_at=datetime.now(timezone.utc), status="unfinished")
    cs.keep_unfinished(cid)
    assert rv.review_counts()["unfinished_captures"] == 1
    with read_session() as s:
        assert s.query(DraftItem).count() == 0  # Escape never invents an issue
    cs.discard(cid)
    with read_session() as s:
        assert s.get(Capture, cid) is None


def test_crash_recovery_repairs_chunks_and_requeues(app_env):
    import struct

    from checkpoint.config import paths
    from checkpoint.db import read_session, write_session
    from checkpoint.models import AudioChunk, AudioSource, Project, Session, SessionPause, TranscriptionJob
    from checkpoint.services.sessions import SessionManager
    from checkpoint.audio.recorder import _wav_header

    with write_session() as s:
        p = Project(name="P")
        s.add(p)
        s.flush()
        sess = Session(project_id=p.id, title="t", state="active")
        s.add(sess)
        s.flush()
        src = AudioSource(session_id=sess.id, kind="mic", label="Me", state="recording")
        s.add(src)
        s.flush()
        rel = f"{sess.id}/mic/00001.wav"
        f = paths().audio / rel
        f.parent.mkdir(parents=True)
        # Simulate a crash: header still says 0 data bytes, 1 s of 16 kHz mono audio follows.
        f.write_bytes(_wav_header(16000, 1, 0) + b"\x01\x00" * 16000)
        s.add(AudioChunk(session_id=sess.id, source_id=src.id, seq=1, rel_path=rel, start_offset_ms=0, sample_rate=16000,
                         channels=1, state="recording"))
        sid = sess.id
    restart()
    rec = SessionManager().recover_on_launch()
    assert rec == [sid]
    with read_session() as s:
        assert s.get(Session, sid).state == "interrupted"
        ch = s.query(AudioChunk).one()
        assert ch.state == "recovered" and ch.duration_ms == 1000
        assert s.query(TranscriptionJob).count() == 1
        assert s.query(SessionPause).filter_by(reason="restart").count() == 1
    data = (paths().audio / ch.rel_path).read_bytes()
    assert struct.unpack("<I", data[40:44])[0] == 32000


def test_data_from_newer_version_gives_clear_message(app_env):
    import pytest
    from sqlalchemy import text

    from checkpoint import db

    with db.engine().begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = '9999'"))
    db.reset_engine()
    with pytest.raises(db.DataNewerThanCode, match="newer version of Checkpoint"):
        db.migrate()
