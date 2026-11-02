"""Completed tasks copy without the menu; edits and explicit copies win races."""
import io
import json
import os
import threading
from unittest.mock import Mock

import pytest
from PIL import Image

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def wait_until(app, predicate):
    from PySide6.QtTest import QTest
    for _ in range(800):
        app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    pytest.fail('Automatic clipboard operation did not finish')


def settle(app, copier):
    wait_until(app, lambda: not copier._running and not copier._pending and not copier._timer.isActive())


def context(app):
    mime = app.clipboard().mimeData()
    assert mime.hasImage() and mime.hasUrls()
    with Image.open(io.BytesIO(bytes(mime.data('image/png')))) as image:
        return image.info['Checkpoint.Context']


@pytest.fixture
def automatic(app_env, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from checkpoint.desktop.clipboard import AutomaticTaskClipboard
    from checkpoint.events import hooks
    app = QApplication.instance() or QApplication([])
    app.clipboard().setText('Previous clipboard')
    copier = AutomaticTaskClipboard()
    class Relay(QObject):
        completed = Signal(str)
    relay = Relay()
    relay.completed.connect(copier.request)
    toast = Mock()
    monkeypatch.setattr(hooks, 'task_ready', relay.completed.emit)
    monkeypatch.setattr(hooks, 'toast', toast)
    try:
        yield app, copier, toast
    finally:
        copier.stop()
        copier._workers.shutdown(wait=True)
        app.processEvents()
        app.clipboard().clear()


def test_saved_task_and_latest_text_and_image_edits_copy_without_menu(automatic):
    from checkpoint.services import review, cropping
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from conftest import png_bytes
    from test_simple_inbox import captured
    app, copier, toast = automatic
    service, cid, _ = captured(None)
    iid = service.save_capture_note(cid, 'Recorded task')['work_item_id']
    settle(app, copier)
    assert 'Recorded task' in context(app)
    toast.assert_called_with('success', 'Added task to clipboard', None)

    review.update_work_item(iid, {'title': 'Corrected title', 'description': 'Corrected transcript'})
    settle(app, copier)
    assert 'Corrected transcript' in context(app)
    with Image.open(io.BytesIO(bytes(app.clipboard().mimeData().data('image/png')))) as image:
        assert json.loads(image.info['Checkpoint.Item'])['item']['title'] == 'Corrected title'
    assert 'Recorded task' not in context(app)

    with read_session() as session:
        sha = session.get(Capture, cid).sha256
    png, _ = png_bytes(color=(220, 30, 40))
    cropping.save_image(cid, png, expected_sha=sha)
    settle(app, copier)
    with Image.open(io.BytesIO(bytes(app.clipboard().mimeData().data('image/png')))) as image:
        assert image.getpixel((500, 64)) == (220, 30, 40)
    assert 'Corrected transcript' in context(app)


def test_older_edits_status_changes_and_idle_do_not_replace_manual_clipboard(automatic):
    from checkpoint.services import review
    from test_simple_inbox import captured
    app, copier, toast = automatic
    service, old, _ = captured(None)
    old_id = service.save_capture_note(old, 'Older task')['work_item_id']
    service, latest, _ = captured(None)
    latest_id = service.save_capture_note(latest, 'Latest task')['work_item_id']
    settle(app, copier)
    assert 'Latest task' in context(app)
    app.clipboard().setText('Manual selection')
    toast.reset_mock()
    review.update_work_item(old_id, {'description': 'Older task changed'})
    review.update_work_item(latest_id, {'work_status': 'done'})
    settle(app, copier)
    assert app.clipboard().text() == 'Manual selection'
    toast.assert_not_called()
    review.update_work_item(latest_id, {'description': 'Latest edited after completion'})
    settle(app, copier)
    assert 'Latest edited after completion' in context(app)


@pytest.mark.parametrize('interruption', ['edit', 'new_task', 'manual_copy', 'shutdown', 'disabled'])
def test_slow_render_cannot_publish_stale_results(automatic, monkeypatch, interruption):
    from checkpoint.desktop import clipboard
    from checkpoint.services import review
    from test_simple_inbox import captured
    app, copier, toast = automatic
    started, release = threading.Event(), threading.Event()
    original = clipboard.task_image_files
    calls = []

    def slow(ids):
        files = original(ids)
        calls.append(ids)
        if len(calls) == 1:
            started.set()
            assert release.wait(8)
        return files

    monkeypatch.setattr(clipboard, 'task_image_files', slow)
    service, cid, _ = captured(None)
    iid = service.save_capture_note(cid, 'Stale text')['work_item_id']
    try:
        wait_until(app, started.is_set)
        if interruption == 'edit':
            review.update_work_item(iid, {'description': 'Newest text'})
        elif interruption == 'new_task':
            service, next_cid, _ = captured(None)
            service.save_capture_note(next_cid, 'Newest text')
        elif interruption == 'manual_copy':
            app.clipboard().setText('Deliberate copy')
        elif interruption == 'disabled':
            from checkpoint.settings_store import update_settings
            update_settings({'capture': {'auto_copy_latest': False}})
        else:
            copier.stop()
        release.set()
        settle(app, copier)
        if interruption in ('edit', 'new_task'):
            assert 'Newest text' in context(app) and 'Stale text' not in context(app)
            assert toast.call_count == 1
        else:
            assert app.clipboard().text() == ('Deliberate copy' if interruption == 'manual_copy' else 'Previous clipboard')
            toast.assert_not_called()
    finally:
        release.set()


def test_render_failure_preserves_clipboard_and_later_edit_retries(automatic, monkeypatch):
    from checkpoint.desktop import clipboard
    from checkpoint.services import review
    from test_simple_inbox import captured
    app, copier, toast = automatic
    original = clipboard.task_image_files
    monkeypatch.setattr(clipboard, 'task_image_files', Mock(side_effect=OSError('Disk unavailable')))
    service, cid, _ = captured(None)
    iid = service.save_capture_note(cid, 'Saved despite copy failure')['work_item_id']
    settle(app, copier)
    assert app.clipboard().text() == 'Previous clipboard'
    assert toast.call_args.args[0] == 'warning'
    assert 'Disk unavailable' in toast.call_args.args[1]
    assert review.get_work_item(iid)['description'] == 'Saved despite copy failure'
    monkeypatch.setattr(clipboard, 'task_image_files', original)
    review.update_work_item(iid, {'description': 'Retry on next edit'})
    settle(app, copier)
    assert 'Retry on next edit' in context(app)


def test_background_session_transcript_refreshes_clipboard(automatic):
    from checkpoint.db import write_session
    from checkpoint.models import TranscriptSegment, new_id
    from checkpoint.services.inbox import reconcile
    from test_note_workflow import auto_observation
    app, copier, _ = automatic
    cid, sid, source = auto_observation(None)
    # Use a manual marker so reconciliation attaches speech without AI extraction.
    from checkpoint.models import Capture
    with write_session() as session:
        session.get(Capture, cid).trigger = 'hotkey'
    reconcile()
    settle(app, copier)
    assert 'The menu overlaps the controls' in context(app)
    with write_session() as session:
        session.add(TranscriptSegment(id=new_id(), session_id=sid, source_id=source, start_ms=13000, end_ms=14500,
                                      text='Especially on smaller screens'))
    reconcile()
    settle(app, copier)
    assert 'Especially on smaller screens' in context(app)


def test_voice_capture_waits_for_processing_and_marshals_worker_completion(automatic):
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.services.voice import retain, finish_note
    from test_note_workflow import wav_bytes
    from test_simple_inbox import captured
    app, copier, toast = automatic
    service, cid, _ = captured(None)
    ensure_capture(cid)
    nid = retain(cid, wav_bytes())
    app.processEvents()
    assert app.clipboard().text() == 'Previous clipboard'
    toast.assert_not_called()

    def finish():
        finish_note(nid, 'Processed voice note', [])
        service.save_capture_note(cid, 'Processed voice note')

    worker = threading.Thread(target=finish)
    worker.start()
    try:
        wait_until(app, lambda: not worker.is_alive())
        settle(app, copier)
        assert 'Processed voice note' in context(app)
        toast.assert_called_once_with('success', 'Added task to clipboard', None)
    finally:
        worker.join()


def test_disabled_automatic_copy_keeps_manual_copy_and_can_be_reenabled(automatic):
    from checkpoint.desktop.clipboard import copy_image_files, task_image_files
    from checkpoint.services import review
    from checkpoint.settings_store import update_settings
    from test_simple_inbox import captured
    app, copier, toast = automatic
    update_settings({'capture': {'auto_copy_latest': False}})
    service, cid, _ = captured(None)
    iid = service.save_capture_note(cid, 'Automatic copying disabled')['work_item_id']
    review.update_work_item(iid, {'description': 'Edited while disabled'})
    settle(app, copier)
    assert app.clipboard().text() == 'Previous clipboard'
    toast.assert_not_called()
    copy_image_files(task_image_files([iid]))
    assert 'Edited while disabled' in context(app)
    update_settings({'capture': {'auto_copy_latest': True}})
    review.update_work_item(iid, {'description': 'Automatic copying reenabled'})
    settle(app, copier)
    assert 'Automatic copying reenabled' in context(app)
    toast.assert_called_once_with('success', 'Added task to clipboard', None)


def test_native_toggle_defaults_on_for_existing_settings_and_persists(app_env, monkeypatch):
    from checkpoint.db import write_session
    from checkpoint.models import Setting
    from checkpoint.settings_store import get_settings, invalidate_cache
    from test_native_desktop import app, settings_dialog
    application = app()
    # Preferences saved before the clipboard setting existed adopt the default.
    with write_session() as session:
        session.add(Setting(key='app', value={'version': 6, 'capture': {'region': 'foreground_window'}}))
    dialog = settings_dialog(monkeypatch)
    try:
        assert dialog.auto_copy_latest.isChecked()
        dialog.auto_copy_latest.setChecked(False)
        dialog._save()
    finally:
        dialog.close()
    invalidate_cache()
    assert not get_settings().capture.auto_copy_latest
    assert get_settings().capture.region == 'foreground_window'
    dialog = settings_dialog(monkeypatch)
    try:
        assert not dialog.auto_copy_latest.isChecked()
        dialog.auto_copy_latest.setChecked(True)
        dialog._save()
    finally:
        dialog.close()
    invalidate_cache()
    assert get_settings().capture.auto_copy_latest
