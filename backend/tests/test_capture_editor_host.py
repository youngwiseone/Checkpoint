"""Capture gestures and recording completion use the shared editing workspace."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPointF

from conftest import png_bytes
from test_native_desktop import app
from test_note_workflow import wav_bytes


@pytest.fixture
def editor_host(app_env, monkeypatch):
    from checkpoint.desktop import host as desktop
    from checkpoint.capture import screen
    from checkpoint.services.capture import CaptureService, default_project_id
    from checkpoint.services.sessions import SessionManager
    from checkpoint.settings_store import update_settings
    application = app()
    pid = default_project_id()
    update_settings({'last_project_id': pid})
    sessions = SessionManager()
    service = CaptureService(sessions)
    core = SimpleNamespace(capture=service, sessions=sessions, inbox=Mock(),
                           worker_status=lambda: {'transcription': {}}, appwatch=Mock())
    monkeypatch.setattr(desktop, 'get_core', lambda: core)
    png, thumb = png_bytes()
    grab = screen.Grab(png, thumb, 320, 180, {}, 'Test app', 0, None, False)
    monkeypatch.setattr(screen, 'grab', Mock(return_value=grab))
    host = desktop.DesktopHost(show_panel=False)
    from checkpoint.events import hooks
    monkeypatch.setattr(hooks, 'request_note', host.bridge.note_requested.emit)
    monkeypatch.setattr(hooks, 'toast', Mock())
    try:
        yield application, host, grab
    finally:
        host.held_timer.stop()
        host.note_window._recording = None
        host.note_window._close()
        host.task_clipboard.stop()
        host.overlay.close()
        host.toast.hide()


@pytest.mark.parametrize('show', [True, False])
@pytest.mark.parametrize('ask_context', [True, False])
def test_capture_popup_setting_applies_with_and_without_session_audio(editor_host, show, ask_context):
    from checkpoint.settings_store import update_settings
    from checkpoint.services.review import list_work_items
    application, host, _ = editor_host
    update_settings({'capture': {'show_editor_on_capture': show}})
    host.core.capture.hotkey_capture(ask_context)
    application.processEvents()
    assert len(list_work_items()) == 1
    assert host.note_window.isVisible() is show
    if show:
        assert host.note_window.image_tabs.count() == 1


def start_voice(host, grab):
    recording = SimpleNamespace(audio=b'', phase='recording', text='', words=[], error='',
        retained_note_id=None, start=Mock(), stop=Mock(), cancel=Mock())
    host._press_recording = recording
    host._start_held_note((grab, 1.0, datetime.now(timezone.utc)))
    return recording


def test_save_during_held_recording_waits_and_keeps_drawing_and_typed_note(editor_host):
    from checkpoint.services.review import get_work_item
    from checkpoint.desktop.screenshot import Mark
    application, host, grab = editor_host
    recording = start_voice(host, grab)
    window = host.note_window
    assert window.isVisible() and 'Listening' in window.dictation_status.text()
    iid, cid = window.item_id, window.payload['id']
    workspace = window.workspaces[cid]
    workspace.image.remember()
    workspace.image.marks.append(Mark('pencil', (QPointF(20, 30), QPointF(100, 30)), '#ff4040', 12))
    window.text.setPlainText('Typed while speaking')
    window._save()
    recording.stop.assert_called_once()
    assert window.isVisible()
    assert get_work_item(iid)['description'] == ''
    recording.audio, recording.phase = wav_bytes(), 'transcribing'
    host._poll_held_notes()
    recording.text, recording.phase = 'The button overlaps the text', 'done'
    host._poll_held_notes()
    assert not window.isVisible()
    assert get_work_item(iid)['description'] == 'Typed while speaking\n\nThe button overlaps the text'
    assert not workspace.dirty
    assert not host._held_jobs


def test_discard_held_recording_cancels_job_and_late_transcript_cannot_return(editor_host, monkeypatch):
    from checkpoint.desktop import editor
    from checkpoint.services.review import get_work_item
    application, host, grab = editor_host
    recording = start_voice(host, grab)
    iid = host.note_window.item_id
    monkeypatch.setattr(editor, 'confirm', Mock(return_value=True))
    host.note_window._discard()
    recording.cancel.assert_called()
    assert not host._held_jobs and not host.note_window.isVisible()
    recording.audio, recording.text, recording.phase = wav_bytes(), 'Late result', 'done'
    host._poll_held_notes()
    assert get_work_item(iid)['work_status'] == 'deleted'
    assert get_work_item(iid)['description'] == ''
    assert not host.note_window.isVisible()


def test_hidden_voice_capture_still_processes_and_explicit_edit_opens(editor_host):
    from checkpoint.services.review import get_work_item
    from checkpoint.services.task_evidence import last_editor_payload
    from checkpoint.settings_store import update_settings
    application, host, grab = editor_host
    update_settings({'capture': {'show_editor_on_capture': False}})
    recording = start_voice(host, grab)
    assert not host.note_window.isVisible()
    payload = host._held_jobs[0][0]
    recording.audio, recording.text, recording.phase = wav_bytes(), 'Quiet capture', 'done'
    host._poll_held_notes()
    assert get_work_item(payload['item_id'])['description'] == 'Quiet capture'
    assert not host.note_window.isVisible()
    host.bridge.note_requested.emit(last_editor_payload(payload['project_id']))
    assert host.note_window.isVisible() and host.note_window.text.toPlainText() == 'Quiet capture'


def test_transcript_finishing_while_taking_second_image_does_not_leave_recording_stuck(editor_host):
    from checkpoint.services.review import get_work_item
    application, host, grab = editor_host
    recording = start_voice(host, grab)
    window = host.note_window
    iid = window.item_id
    window.text.setPlainText('Typed before taking another screenshot')
    # snip_into temporarily hides this same editor, retaining all its drafts.
    window._snip_pending = True
    window.hide()
    recording.audio, recording.text, recording.phase = wav_bytes(), 'Transcript finished while snipping', 'done'
    host._poll_held_notes()
    assert not host._held_jobs and window._recording is None
    window._snip_pending = False
    window.show()
    window._save()
    assert not window.isVisible()
    assert get_work_item(iid)['description'] == 'Typed before taking another screenshot\n\nTranscript finished while snipping'


def test_local_dictation_image_cannot_be_deleted_before_recording_finishes(editor_host, monkeypatch):
    from checkpoint.desktop import editor
    from checkpoint.services.review import get_work_item, list_work_items
    application, host, _ = editor_host
    payload = host.core.capture.hotkey_capture(True)
    application.processEvents()
    window = host.note_window
    window._dictation = SimpleNamespace(cancel=Mock())
    window._dictation_capture = payload['id']
    confirmation = Mock(return_value=True)
    monkeypatch.setattr(editor, 'confirm', confirmation)
    window._remove_image(0)
    confirmation.assert_not_called()
    assert 'Finish the recording' in window.error.text()
    assert get_work_item(window.item_id)['capture_count'] == 1
    assert len(list_work_items()) == 1


def test_queued_save_waits_for_second_screenshot_before_closing(editor_host, monkeypatch):
    from checkpoint.desktop import snipping
    from checkpoint.services.review import get_work_item
    application, host, grab = editor_host
    recording = start_voice(host, grab)
    window = host.note_window
    iid = window.item_id
    window.text.setPlainText('Keep the screenshot taken while processing')
    window._save()
    callbacks = []
    def snip(item_id, parent, done):
        parent.hide()
        callbacks.append(done)
    monkeypatch.setattr(snipping, 'snip_into', snip)
    window._snip()
    recording.audio, recording.text, recording.phase = wav_bytes(), 'Finished voice', 'done'
    host._poll_held_notes()
    assert window._save_after_snip and window.payload['item_id'] == iid
    assert not window.isVisible()
    from test_unified_capture_editor import append_image
    append_image(host.core.capture, iid)
    callbacks[0](None)
    window.show()  # snip_into restores the editor after its completion callback.
    application.processEvents()
    assert not window.isVisible()
    assert get_work_item(iid)['capture_count'] == 2
    assert 'Finished voice' in get_work_item(iid)['description']


def test_double_tap_f9_uses_same_editor_when_automatic_open_is_disabled(editor_host):
    from checkpoint.desktop.editor import CaptureEditor
    from checkpoint.desktop.gestures import CaptureButton
    from checkpoint.services.review import list_work_items
    from checkpoint.settings_store import update_settings
    from test_capture_gestures import Scheduler
    application, host, _ = editor_host
    update_settings({'capture': {'show_editor_on_capture': False}})
    payload = host.core.capture.hotkey_capture(True)
    host.core.capture.save_capture_note(payload['id'], 'Reopen this task')
    assert not host.note_window.isVisible()
    scheduler = Scheduler()
    host.capture_button = CaptureButton(scheduler.schedule)
    host._on_hotkey('capture')
    host._on_hotkey('capture')
    application.processEvents()
    assert isinstance(host.note_window, CaptureEditor)
    assert host.note_window.isVisible()
    assert host.note_window.text.toPlainText() == 'Reopen this task'
    assert len(list_work_items()) == 1


def test_f9_focuses_note_for_typing_and_keeps_marker_ready(editor_host):
    from PySide6.QtTest import QTest
    from checkpoint.desktop.gestures import CaptureButton
    from test_capture_gestures import Scheduler
    from test_screenshot_editor import at, gesture
    application, host, _ = editor_host
    scheduler = Scheduler()
    host.capture_button = CaptureButton(scheduler.schedule)
    host._on_hotkey('press')
    host._on_hotkey('capture')
    scheduler.flush()
    application.processEvents()
    window = host.note_window
    assert application.focusWidget() is window.text
    QTest.keyClicks(application.focusWidget(), 'Type immediately after F9')
    assert window.text.toPlainText() == 'Type immediately after F9'
    workspace = window.images.currentWidget()
    assert workspace.tool_buttons['pencil'].isChecked()
    assert workspace.image.tool == 'pencil' and workspace.image.stroke_width == 12
    gesture(workspace.image, at(workspace.image, 20, 30), at(workspace.image, 100, 30))
    assert len(workspace.image.marks) == 1
    assert workspace.image.hasFocus()
    assert window.text.toPlainText() == 'Type immediately after F9'


def test_held_voice_capture_focuses_drawing_instead_of_note(editor_host):
    application, host, grab = editor_host
    start_voice(host, grab)
    application.processEvents()
    window = host.note_window
    assert window.images.currentWidget().image.hasFocus()
    assert window.images.currentWidget().tool_buttons['pencil'].isChecked()


def test_enter_in_note_does_not_apply_pending_crop_or_save(editor_host):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    from test_screenshot_editor import at, gesture
    application, host, _ = editor_host
    host.core.capture.hotkey_capture(True)
    application.processEvents()
    window = host.note_window
    workspace = window.images.currentWidget()
    workspace.set_tool('crop')
    gesture(workspace.image, at(workspace.image, 10, 10), at(workspace.image, 160, 100))
    before = workspace.image.view.getRect()
    assert workspace.image.selection is not None
    window.text.setPlainText('First line')
    window.text.moveCursor(QTextCursor.MoveOperation.End)
    window.text.setFocus()
    QTest.keyClick(window.text, Qt.Key.Key_Return)
    assert window.text.toPlainText() == 'First line\n'
    assert workspace.image.view.getRect() == before
    assert workspace.image.selection is not None and window.isVisible()
