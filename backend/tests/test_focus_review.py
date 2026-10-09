import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_review_navigation_done_archive_and_conflict_safe_undo(app_env):
    from PySide6.QtWidgets import QApplication
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item, get_work_item, update_work_item

    app = QApplication.instance() or QApplication([])
    pid = default_project_id()
    first = create_work_item(pid, "bug", "First", "Details")
    second = create_work_item(pid, "bug", "Second", "")
    panel = InboxPanel(lambda *args: None)
    panel.toggle(pid)
    app.processEvents()
    selected = panel.selected_id
    panel._step(1)
    assert panel.selected_id != selected
    target = panel.selected_id
    panel._status("done")
    assert get_work_item(target)["work_status"] == "done"
    assert panel.selected_id != target
    panel._undo()
    assert panel.selected_id == target
    assert get_work_item(target)["work_status"] == "open"
    assert panel.undo_action is None
    panel._status("wont_do")
    update_work_item(target, {"title": "Edited elsewhere"})
    panel._undo()
    assert get_work_item(target)["work_status"] == "wont_do"
    assert "changed elsewhere" in panel.message.text()
    assert {first["id"], second["id"]} == {target, panel.selected_id}
    panel.close()


def test_highlight_preserves_crop_original_and_chat_image(app_env):
    from PIL import Image
    from test_simple_inbox import captured
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from checkpoint.services.cropping import crop, original_path
    from checkpoint.services.handoff import build
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.storage import media_path

    _, cid, original = captured(app_env)
    wid = ensure_capture(cid)
    crop(cid, (0, 0, 100, 80))
    before = build([wid], True, True).images[0][1]
    crop(cid, (10, 10, 40, 30), highlight=True)
    with read_session() as s:
        capture = s.get(Capture, cid)
        assert original_path(capture).read_bytes() == original
        with Image.open(media_path(capture.image_rel_path)) as image:
            assert image.size == (100, 80)
            assert image.getpixel((10, 10)) == (255, 64, 64)
            assert image.getpixel((25, 25)) == (40, 90, 160)
    after = build([wid], True, True).images[0][1]
    assert after != before
    crop(cid, None)
    with Image.open(build([wid], True, True).images[0][1]) as image:
        assert image.size == (320, 180)
        assert all(abs(a - b) <= 2 for a, b in zip(image.getpixel((10, 10)), (40, 90, 160)))  # chat images are JPEG


def test_highlight_api_auth_and_bounds(app_env):
    from fastapi.testclient import TestClient
    from checkpoint.api.app import create_app
    from checkpoint.api.security import AuthState
    from test_simple_inbox import captured

    _, cid, _ = captured(app_env)
    auth = AuthState(8878, [])
    with TestClient(create_app(auth), base_url="http://127.0.0.1:8878") as client:
        route = f"/api/captures/{cid}/highlight"
        headers = {"X-Checkpoint-Request": "1"}
        box = {"x": 10, "y": 10, "width": 40, "height": 30}
        assert client.post(route, json=box, headers=headers).status_code == 401
        client.get(auth.open_url())
        assert client.post(route, json=box, headers=headers).status_code == 200
        assert client.post(route, json={**box, "width": 999}, headers=headers).status_code == 400
        assert client.post(route, json={**box, "x": .5}, headers=headers).status_code == 422


def test_focus_panel_loads_screenshot(app_env):
    from PySide6.QtWidgets import QApplication, QLabel
    from test_simple_inbox import captured
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.inbox import ensure_capture

    app = QApplication.instance() or QApplication([])
    _, cid, _ = captured(app_env)
    wid = ensure_capture(cid)
    panel = InboxPanel(lambda *args: None)
    panel.toggle(default_project_id())
    app.processEvents()
    assert panel.selected_id == wid
    assert not panel.deck.image.isNull()
    panel.close()
