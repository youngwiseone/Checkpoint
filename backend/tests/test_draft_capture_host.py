"""Double-tap edit screenshots stay temporary until the draft tab is accepted."""
from unittest.mock import Mock

from conftest import png_bytes
from test_capture_editor_host import editor_host
from test_capture_gestures import Scheduler


def previous_task(host):
    from checkpoint.settings_store import update_settings
    update_settings({'capture': {'show_editor_on_capture': False}})
    payload = host.core.capture.hotkey_capture(True)
    iid = host.core.capture.save_capture_note(payload['id'], 'Previous capture')['work_item_id']
    return iid, payload['id']


def double_tap(host):
    from checkpoint.desktop.gestures import CaptureButton
    scheduler = Scheduler()
    host.capture_button = CaptureButton(scheduler.schedule)
    host._on_hotkey('press')
    host._on_hotkey('capture')
    host._on_hotkey('press')
    host._on_hotkey('capture')
    scheduler.flush()


def test_double_tap_freezes_one_new_frame_without_adding_media_until_clicked(editor_host, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from checkpoint.capture import screen
    from checkpoint.config import paths
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from checkpoint.services.review import get_work_item, list_work_items
    application, host, _ = editor_host
    iid, old_cid = previous_task(host)
    existing_files = set(paths().media.rglob('*'))
    png, thumb = png_bytes(color=(220, 30, 40))
    monitor = {'focus': {'cursor': [40, 20], 'center': [160, 90], 'units': 'image_pixels'}}
    frame = screen.Grab(png, thumb, 320, 180, monitor, 'New frame', 0, None, False)
    grab = Mock(return_value=frame)
    monkeypatch.setattr(screen, 'grab', grab)
    double_tap(host)
    application.processEvents()
    window = host.note_window
    assert window.isVisible() and window.item_id == iid
    assert window.text.toPlainText() == 'Previous capture'
    assert window.image_tabs.count() == 2
    assert 'Draft' in window.image_tabs.tabText(1)
    assert window.image_tabs.tabData(0) == old_cid
    assert window.image_tabs.currentIndex() == 0
    assert window._draft_capture['png'] == png
    assert get_work_item(iid)['capture_count'] == len(list_work_items()) == 1
    assert set(paths().media.rglob('*')) == existing_files
    grab.assert_called_once()
    QTest.mouseClick(window.image_tabs, Qt.MouseButton.LeftButton, pos=window.image_tabs.tabRect(1).center())
    application.processEvents()
    assert window._draft_capture is None
    assert get_work_item(iid)['capture_count'] == 2
    cid = window.image_tabs.tabData(1)
    assert cid != old_cid and cid in window.workspaces
    with read_session() as session:
        accepted = session.get(Capture, cid)
        assert accepted.window_title == 'New frame'
        assert accepted.monitor == monitor
        assert accepted.trigger == 'edit'
    assert len(list_work_items()) == 1


def test_new_double_tap_replaces_unaccepted_draft_after_close(editor_host, monkeypatch):
    from checkpoint.capture import screen
    from checkpoint.config import paths
    from checkpoint.services.review import get_work_item
    application, host, _ = editor_host
    iid, _ = previous_task(host)
    existing_files = set(paths().media.rglob('*'))
    first, first_thumb = png_bytes(color=(200, 10, 10))
    second, second_thumb = png_bytes(color=(10, 200, 10))
    frames = [screen.Grab(first, first_thumb, 320, 180, {}, 'First', 0, None, False),
              screen.Grab(second, second_thumb, 320, 180, {}, 'Second', 0, None, False)]
    monkeypatch.setattr(screen, 'grab', Mock(side_effect=frames))
    double_tap(host)
    application.processEvents()
    assert host.note_window._draft_capture['png'] == first
    host.note_window._close()
    assert host.note_window._draft_capture is None
    double_tap(host)
    application.processEvents()
    assert host.note_window.image_tabs.count() == 2
    assert host.note_window._draft_capture['png'] == second
    assert get_work_item(iid)['capture_count'] == 1
    assert set(paths().media.rglob('*')) == existing_files


def test_draft_capture_failure_still_opens_previous_task_without_draft(editor_host, monkeypatch):
    from checkpoint.capture import screen
    from checkpoint.events import hooks
    from checkpoint.services.review import get_work_item
    application, host, _ = editor_host
    iid, _ = previous_task(host)
    monkeypatch.setattr(screen, 'grab', Mock(side_effect=OSError('Display unavailable')))
    double_tap(host)
    application.processEvents()
    assert host.note_window.isVisible() and host.note_window.item_id == iid
    assert host.note_window._draft_capture is None
    assert host.note_window.image_tabs.count() == get_work_item(iid)['capture_count'] == 1
    assert 'draft screenshot' in hooks.toast.call_args.args[1]


def test_first_double_tap_creates_one_normal_capture_without_an_extra_draft(editor_host):
    from checkpoint.services.review import list_work_items
    application, host, _ = editor_host
    double_tap(host)
    application.processEvents()
    assert len(list_work_items()) == 1
    assert host.note_window.isVisible()
    assert host.note_window._draft_capture is None
    assert host.note_window.image_tabs.count() == 1


def test_automatic_clipboard_excludes_draft_until_its_tab_is_clicked(editor_host, monkeypatch):
    import io
    import json
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from checkpoint.events import hooks
    from test_automatic_clipboard import settle
    application, host, _ = editor_host
    monkeypatch.setattr(hooks, 'task_ready', host.bridge.task_ready.emit)
    iid, _ = previous_task(host)
    settle(application, host.task_clipboard)

    def copied_image_count():
        mime = application.clipboard().mimeData()
        assert mime.hasImage() and mime.hasUrls()
        with Image.open(io.BytesIO(bytes(mime.data('image/png')))) as image:
            return json.loads(image.info['Checkpoint.Item'])['image_count']

    assert copied_image_count() == 1
    double_tap(host)
    application.processEvents()
    window = host.note_window
    assert window._draft_capture is not None
    # Background task refreshes use saved evidence, never temporary editor tabs.
    hooks.emit_task_ready(iid)
    settle(application, host.task_clipboard)
    assert window._draft_capture is not None
    assert copied_image_count() == 1
    QTest.mouseClick(window.image_tabs, Qt.MouseButton.LeftButton, pos=window.image_tabs.tabRect(1).center())
    settle(application, host.task_clipboard)
    assert window._draft_capture is None
    assert copied_image_count() == 2
    application.clipboard().clear()
