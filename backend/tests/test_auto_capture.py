"""Auto-capture: the model's picks save the buffered frame from when the words were said."""

import io
import time

from PIL import Image


def _jpeg():
    b = io.BytesIO()
    Image.new("RGB", (320, 180), (200, 40, 40)).save(b, "JPEG")
    return b.getvalue()


def _setup(monkeypatch, mode="live"):
    from checkpoint.capture import screen
    from checkpoint.capture.auto import AutoCaptureWorker
    from checkpoint.db import write_session
    from checkpoint.extraction import ollama
    from checkpoint.models import AudioSource, Project
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager, StartSessionRequest
    from checkpoint.settings_store import update_settings

    monkeypatch.setattr(ollama, "ensure_running", lambda *a, **k: True)
    update_settings({"auto_capture": {"enabled": True, "cooldown_s": 15}})
    with write_session() as s:
        p = Project(name="Game")
        s.add(p)
        s.flush()
        pid = p.id
    sm = SessionManager()
    sid = sm.start(StartSessionRequest(project_id=pid))
    sm.active.transcription_mode = mode
    with write_session() as s:
        src = AudioSource(session_id=sid, kind="mic")
        s.add(src)
        s.flush()
        src_id = src.id
    w = AutoCaptureWorker(sm, CaptureService(sm))
    w.frames.start = lambda target: None  # no real screen grabs in tests
    now = time.monotonic()
    jpeg = _jpeg()
    mon = {"id": "m1", "left": 0, "top": 0, "width": 320, "height": 180}
    for back in range(80, -1, -2):
        w.frames.add(screen.Frame(mono=now - back, jpeg=jpeg, fg=None, region=dict(mon), monitor=mon))
    return sm, w, sid, src_id, now


def _segment(sid, src_id, start_ms, text):
    import uuid

    from checkpoint.db import write_session
    from checkpoint.models import TranscriptSegment

    with write_session() as s:
        s.add(TranscriptSegment(id=str(uuid.uuid4()), session_id=sid, source_id=src_id, start_ms=start_ms, end_ms=start_ms + 1500, text=text))


def _auto_caps(sid):
    from sqlalchemy import select

    from checkpoint.db import read_session
    from checkpoint.models import Capture

    with read_session() as s:
        return [(c.offset_ms, c.status) for c in s.scalars(select(Capture).where(Capture.session_id == sid, Capture.trigger == "auto")
                                                             .order_by(Capture.offset_ms)).all()]


def test_picks_frame_from_when_words_were_said(app_env, monkeypatch):
    sm, w, sid, src_id, now = _setup(monkeypatch)
    at = lambda back: sm.active.clock.offset_ms(now - back)  # noqa: E731
    _segment(sid, src_id, at(60), "whoa that's a bug, the enemy fell through the floor")
    _segment(sid, src_id, at(52), "another bug right after")  # inside the 15 s cooldown
    _segment(sid, src_id, at(30), "ok heading to the next level now")
    _segment(sid, src_id, at(10), "bug again, the score reset")
    asked = []
    w.decide = lambda text, before: asked.append(text) or "bug" in text
    w.step()
    caps = _auto_caps(sid)
    assert [st for _, st in caps] == ["marker", "marker"]
    assert abs(caps[0][0] - at(60)) <= 1100 and abs(caps[1][0] - at(10)) <= 1100
    assert len(asked) == 4 and w.count == 2
    w.step()  # already-decided lines are never asked about or captured twice
    assert len(asked) == 4 and len(_auto_caps(sid)) == 2
    sm.end()


def test_waits_for_live_transcription(app_env, monkeypatch):
    sm, w, sid, src_id, now = _setup(monkeypatch, mode="after")
    _segment(sid, src_id, sm.active.clock.offset_ms(now - 5), "that's a bug")
    w.decide = lambda text, before: True
    w.step()
    assert w.state == "needs_live" and _auto_caps(sid) == []
    sm.end()


def test_frame_buffer_drops_old_frames(app_env):
    from checkpoint.capture import screen
    from checkpoint.capture.auto import FrameBuffer
    from checkpoint.settings_store import update_settings

    update_settings({"auto_capture": {"buffer_seconds": 30}})
    fb = FrameBuffer()
    for t in range(0, 100, 2):
        fb.add(screen.Frame(mono=float(t), jpeg=b"", fg=None, region={}, monitor={}))
    assert fb.oldest() == 68.0
    assert fb.nearest(80.9, 3).mono == 80.0
    assert fb.nearest(10.0, 3) is None
