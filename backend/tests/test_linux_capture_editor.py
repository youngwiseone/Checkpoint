"""Linux commands use the current editor and preserve pixel coordinates on HiDPI."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from test_capture_editor_host import editor_host
from test_capture_gestures import Scheduler


def test_linux_capture_commands_keep_drafts_in_the_shared_editor(editor_host):
    from checkpoint.desktop.editor import CaptureEditor
    from checkpoint.desktop.gestures import CaptureButton
    from checkpoint.desktop.shortcuts_linux import ShortcutListener
    from checkpoint.services.review import get_work_item, list_work_items
    from checkpoint.settings_store import update_settings

    application, host, _ = editor_host
    update_settings({'capture': {'show_editor_on_capture': False}})
    payload = host.core.capture.hotkey_capture(True)
    iid = host.core.capture.save_capture_note(payload['id'], 'Keep the newer editor')['work_item_id']
    scheduler = Scheduler()
    host.capture_button = CaptureButton(scheduler.schedule)
    listener = ShortcutListener(lambda: {}, host._on_hotkey)
    for _ in range(2):
        for event in listener._events('capture'):
            host._on_hotkey(event)
    scheduler.flush()
    application.processEvents()

    window = host.note_window
    assert isinstance(window, CaptureEditor) and window.isVisible()
    assert window.item_id == iid and window.text.toPlainText() == 'Keep the newer editor'
    assert window.image_tabs.count() == 2 and window._draft_capture is not None
    assert get_work_item(iid)['capture_count'] == len(list_work_items()) == 1


def test_linux_voice_command_records_and_next_press_stops(editor_host, monkeypatch):
    from checkpoint.audio import dictation
    from checkpoint.desktop.gestures import CaptureButton
    from checkpoint.desktop.shortcuts_linux import ShortcutListener
    from checkpoint.settings_store import update_settings

    application, host, _ = editor_host
    update_settings({'audio': {'mic_enabled': True}})
    recording = SimpleNamespace(audio=b'', phase='recording', text='', words=[], error='',
                               retained_note_id=None, start=Mock(), stop=Mock(), cancel=Mock())
    monkeypatch.setattr(dictation, 'NoteDictation', Mock(return_value=recording))
    scheduler = Scheduler()
    host.capture_button = CaptureButton(scheduler.schedule)
    listener = ShortcutListener(lambda: {}, host._on_hotkey)
    for event in listener._events('voice'):
        host._on_hotkey(event)
    application.processEvents()
    recording.start.assert_called_once()
    assert host.note_window._recording is recording
    assert len(host._held_jobs) == 1
    for event in listener._events('voice'):
        host._on_hotkey(event)
    recording.stop.assert_called_once()


@pytest.mark.parametrize('buffered', [False, True])
def test_portal_focus_describes_physical_image_pixels(monkeypatch, buffered):
    from checkpoint.capture import screen

    region = {'left': -640, 'top': 0, 'width': 640, 'height': 360}
    monitor = {**region, 'index': 1, 'id': 'display', 'primary': True}
    monkeypatch.setattr(screen.linux, 'use_portal', lambda: True)
    monkeypatch.setattr(screen, 'list_monitors', lambda: [monitor])
    monkeypatch.setattr(screen.win32, 'foreground_info', lambda: None)
    monkeypatch.setattr(screen.win32, 'cursor_position', lambda: None)
    monkeypatch.setattr(screen.linux, 'grab', lambda selected: Image.new('RGB', (1280, 720)))
    result = screen.frame_to_grab(screen.grab_frame(1.0)) if buffered else screen.grab()
    assert (result.width, result.height) == (1280, 720)
    assert result.monitor['region'] == region
    assert result.monitor['region_units'] == 'logical'
    assert result.monitor['focus'] == {'cursor': None, 'center': [640, 360], 'units': 'image_pixels'}


def test_portal_snipping_uses_logical_region_without_scaling_twice(monkeypatch):
    from PySide6.QtCore import QRect
    from checkpoint.capture.screen import Grab
    from checkpoint.desktop import snipping
    from conftest import png_bytes
    from test_native_desktop import app

    application = app()
    png, thumb = png_bytes(1280, 720)
    region = {'left': -640, 'top': 0, 'width': 640, 'height': 360}
    grab = Grab(png, thumb, 1280, 720, {'region': region, 'region_units': 'logical'}, '', 0, None, False)
    physical_conversion = Mock(side_effect=AssertionError('Portal coordinates are already logical'))
    monkeypatch.setattr(snipping, 'logical_point_for_physical', physical_conversion)
    overlay = snipping.SnippingOverlay(grab, Mock())
    try:
        application.processEvents()
        assert overlay.geometry() == QRect(-640, 0, 640, 360)
        physical_conversion.assert_not_called()
    finally:
        overlay.close()
