from datetime import datetime, timezone

import pytest
from PIL import Image

from conftest import png_bytes, restart


def captured(app_env, session_id=None):
    from checkpoint.services.capture import CaptureService, default_project_id
    from checkpoint.services.sessions import SessionManager
    service = CaptureService(SessionManager())
    png, thumb = png_bytes()
    cid = service.import_image(png, thumb, 320, 180, project_id=default_project_id(), session_id=session_id,
                               offset_ms=10000 if session_id else None, taken_at=datetime.now(timezone.utc), status="marker")
    return service, cid, png


def test_crop_restore_survives_restart_and_handoff_uses_crop(app_env):
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from checkpoint.services.cropping import crop, original_path
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.services.handoff import build
    from checkpoint.storage import media_path
    _, cid, png = captured(app_env)
    wid = ensure_capture(cid)
    crop(cid, (10, 20, 100, 80))
    restart()
    with read_session() as s:
        c = s.get(Capture, cid)
        assert (c.width, c.height) == (100, 80)
        assert original_path(c).read_bytes() == png
        with Image.open(media_path(c.image_rel_path)) as image:
            assert image.size == (100, 80)
    bundle = build([wid], True, True)
    assert bundle.images
    with Image.open(bundle.images[0][1]) as image:
        assert image.size == (100, 80)
    crop(cid, (30, 40, 200, 120))  # second crop is relative to the original, not the first crop
    crop(cid, None)
    restored = build([wid], True, True)
    with Image.open(restored.images[0][1]) as image:
        assert image.size == (320, 180)  # cached assistant images must change with the crop
    with read_session() as s:
        c = s.get(Capture, cid)
        assert (c.width, c.height) == (320, 180)
        with Image.open(media_path(c.image_rel_path)) as image:
            assert image.size == (320, 180)


def test_invalid_crop_leaves_image_untouched(app_env):
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from checkpoint.services.cropping import crop
    from checkpoint.storage import media_path
    _, cid, png = captured(app_env)
    for box in ((-1, 0, 10, 10), (0, 0, 0, 1), (310, 0, 20, 10)):
        with pytest.raises(ValueError):
            crop(cid, box)
    with read_session() as s:
        c = s.get(Capture, cid)
        assert media_path(c.image_rel_path).read_bytes() == png


def test_crop_api_original_media_and_security(app_env):
    import io
    from fastapi.testclient import TestClient
    from checkpoint.api.app import create_app
    from checkpoint.api.security import AuthState
    _, cid, png = captured(app_env)
    auth = AuthState(8878, [])
    with TestClient(create_app(auth), base_url="http://127.0.0.1:8878") as client:
        headers = {"X-Checkpoint-Request": "1"}
        route = f"/api/captures/{cid}/crop"
        assert client.post(route, json={"x": 0, "y": 0, "width": 50, "height": 60}, headers=headers).status_code == 401
        client.get(auth.open_url())
        result = client.post(route, json={"x": 0, "y": 0, "width": 50, "height": 60}, headers=headers)
        assert result.status_code == 200
        original = client.get(f"/api/media/captures/{cid}/original")
        assert original.status_code == 200 and original.content == png
        image = client.get(f"/api/media/captures/{cid}/image")
        assert image.headers["cache-control"] == "no-store"
        with Image.open(io.BytesIO(image.content)) as cropped:
            assert cropped.size == (50, 60)
        assert client.post(route, json={"x": .5, "y": 0, "width": 50, "height": 60}, headers=headers).status_code == 422
        assert client.post(route + "/reset", json={}, headers=headers).status_code == 200


def test_note_on_capture_updates_one_item(app_env):
    from checkpoint.services.inbox import ensure_capture, reconcile
    from checkpoint.services.review import get_work_item, list_work_items
    service, cid, _ = captured(app_env)
    wid = ensure_capture(cid)
    result = service.save_capture_note(cid, "The button is broken")
    reconcile()
    assert result["work_item_id"] == wid
    assert len(list_work_items()) == 1
    assert get_work_item(wid)["description"] == "The button is broken"


def test_pending_cards_migrate_once_without_restoring_dismissed(app_env):
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_draft, dismiss, list_work_items
    from checkpoint.services.inbox import reconcile
    pid = default_project_id()
    create_draft(pid, None, "task", "Keep this", "", origin="manual")
    other = create_draft(pid, None, "task", "Dismissed", "", origin="manual")
    dismiss(other["id"])
    _, cid, _ = captured(app_env)
    screenshot_draft = create_draft(pid, None, "note", "Dismissed screenshot", "", capture_ids=[cid])
    dismiss(screenshot_draft["id"])
    reconcile()
    reconcile()
    assert [w["title"] for w in list_work_items()] == ["Keep this"]


def test_late_transcript_attaches_without_overwriting_user_note(app_env):
    from checkpoint.db import write_session
    from checkpoint.models import Project, Session, AudioSource, TranscriptSegment
    from checkpoint.services.inbox import ensure_capture, reconcile
    from checkpoint.services.review import get_work_item, update_work_item
    with write_session() as s:
        p = Project(name="Speech")
        s.add(p)
        s.flush()
        session = Session(project_id=p.id, title="Try it", state="ended")
        s.add(session)
        s.flush()
        source = AudioSource(session_id=session.id, kind="mic", label="Me")
        s.add(source)
        s.flush()
        sid, source_id = session.id, source.id
    _, cid, _ = captured(app_env, sid)
    wid = ensure_capture(cid)
    update_work_item(wid, {"title": "My title", "description": "My note"})
    with write_session() as s:
        s.add(TranscriptSegment(id="test-speech", session_id=sid, source_id=source_id, start_ms=9000, end_ms=12000,
                                text="This button is broken", model="test"))
    reconcile()
    reconcile()
    item = get_work_item(wid)
    assert item["title"] == "My title" and item["description"] == "My note"
    speech = [e for e in item["evidence"] if e["kind"] == "segment"]
    assert len(speech) == 1 and speech[0]["text"] == "This button is broken"
