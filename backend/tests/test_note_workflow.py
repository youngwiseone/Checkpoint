"""Evidence survives bundling, replay, background correction and editor reopening."""
import io
import json
import wave
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from sqlalchemy import select

from conftest import png_bytes, restart
from test_simple_inbox import captured


def wav_bytes(seconds=2):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * int(16000 * seconds))
    return output.getvalue()


def test_bundle_preserves_all_images_notes_and_one_png(app_env):
    from checkpoint.services.inbox import ensure_capture, reconcile
    from checkpoint.services.task_evidence import bundle_previous, detach_capture
    from checkpoint.services.review import get_work_item, list_work_items
    from checkpoint.services.image_cards import refresh
    service, first, _ = captured(app_env)
    previous = ensure_capture(first)
    service.save_capture_note(first, 'Button overlaps the text')
    _, second, _ = captured(app_env)
    current = ensure_capture(second)
    service.save_capture_note(second, 'Also happens in the settings screen')
    assert bundle_previous(current) == previous
    reconcile()
    restart()
    item = get_work_item(previous)
    assert len(list_work_items(statuses=['open'])) == 1
    assert item['capture_count'] == 2
    assert 'settings screen' in item['description']
    folder = refresh()
    cards = list(folder.rglob(f'{previous}-*.png'))
    assert len(cards) == 1
    with Image.open(cards[0]) as image:
        assert json.loads(image.info['Checkpoint.Item'])['image_count'] == 2
        assert image.getpixel((500, 64)) == (40, 90, 160)
        assert image.getpixel((500, 64 + 180 + 64)) == (40, 90, 160)
    detach_capture(previous, second)
    reconcile()
    assert get_work_item(previous)['capture_count'] == 1
    refresh()
    with Image.open(cards[0]) as image:
        assert json.loads(image.info['Checkpoint.Item'])['image_count'] == 1


def test_bundle_rejects_cross_project_and_self(app_env):
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item
    from checkpoint.services.task_evidence import bundle
    from checkpoint.db import write_session
    from checkpoint.models import Project
    first = create_work_item(default_project_id(), 'note', 'First', '')
    with write_session() as s:
        other = Project(name='Other')
        s.add(other); s.flush()
        other_id = other.id
    second = create_work_item(other_id, 'note', 'Second', '')
    with pytest.raises(ValueError):
        bundle(first['id'], second['id'])
    with pytest.raises(ValueError):
        bundle(first['id'], first['id'])


def test_snip_import_attaches_without_an_extra_task(app_env):
    from checkpoint.services.inbox import ensure_capture, reconcile
    from checkpoint.services.review import get_work_item, list_work_items
    from checkpoint.models import utcnow
    service, first, _ = captured(app_env)
    iid = ensure_capture(first)
    item = get_work_item(iid)
    png, thumb = png_bytes(70, 40)
    cid = service.import_image(png, thumb, 70, 40, project_id=item['project_id'], session_id=None,
        offset_ms=None, taken_at=utcnow(), status='saved', trigger='snip', attach_to=iid)
    reconcile()
    assert len(list_work_items()) == 1
    image = next(e for e in get_work_item(iid)['evidence'] if e.get('capture_id') == cid)
    assert image['focus'] is None and image['width'] == 70


def test_voice_retained_before_text_and_can_replay_selected_words(app_env):
    from checkpoint.services.voice import retain, finish_note, evidence_audio
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.services.review import get_work_item
    _, cid, _ = captured(app_env)
    iid = ensure_capture(cid)
    audio = wav_bytes(4)
    nid = retain(cid, audio)
    finish_note(nid, 'Menu overlaps', [{'text': 'Menu', 'start': 1, 'end': 1.2, 'low': True}])
    restart()
    note = next(e for e in get_work_item(iid)['evidence'] if e['kind'] == 'note')
    assert note['audio_url'] and note['low_confidence']
    assert evidence_audio(note) == audio
    with wave.open(io.BytesIO(evidence_audio({**note, 'start_s': 1, 'end_s': 1.2}))) as wav:
        assert wav.getnframes() == 19200


def test_focus_coordinates_are_relative_and_crops_omit_them(app_env):
    from checkpoint.capture.screen import focus_coordinates
    from checkpoint.db import write_session
    from checkpoint.models import Capture
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.services.review import get_work_item
    from checkpoint.services.cropping import crop
    focus = focus_coordinates({'left': -1600, 'top': 200, 'width': 320, 'height': 180}, (-1580, 240))
    assert focus['cursor'] == [20, 40] and focus['center'] == [160, 90]
    assert focus_coordinates({'left': 0, 'top': 0, 'width': 320, 'height': 180}, (-1, 2))['cursor'] is None
    _, cid, _ = captured(app_env)
    iid = ensure_capture(cid)
    with write_session() as s:
        s.get(Capture, cid).monitor = {'focus': focus}
    assert get_work_item(iid)['evidence'][0]['focus'] == focus
    crop(cid, (0, 0, 100, 80))
    assert get_work_item(iid)['evidence'][0]['focus'] is None
    crop(cid, None)
    assert get_work_item(iid)['evidence'][0]['focus'] == focus


def auto_observation(app_env, text='The menu overlaps the controls', at=10000):
    from checkpoint.db import write_session
    from checkpoint.models import AudioSource, Session, TranscriptSegment, Capture, utcnow, new_id
    from checkpoint.services.capture import default_project_id
    pid = default_project_id()
    with write_session() as s:
        session = s.scalars(select(Session)).first()
        if session is None:
            session = Session(project_id=pid, title='Test', state='active', transcription_mode='live')
            s.add(session); s.flush()
            source = AudioSource(session_id=session.id, kind='mic', label='Me')
            s.add(source); s.flush()
        else:
            source = s.scalars(select(AudioSource).where(AudioSource.session_id == session.id, AudioSource.kind == 'mic')).first()
        sid, source_id = session.id, source.id
        s.add(TranscriptSegment(id=new_id(), session_id=sid, source_id=source_id, start_ms=at, end_ms=at + 2000, text=text))
    _, cid, _ = captured(app_env, sid)
    with write_session() as s:
        c = s.get(Capture, cid)
        c.trigger, c.reason, c.status, c.offset_ms = 'auto', text, 'marker', at
        c.created_at = utcnow()
    return cid, sid, source_id


def test_automatic_capture_waits_for_later_context_and_respects_retraction(app_env, monkeypatch):
    from checkpoint.db import write_session, read_session
    from checkpoint.models import Capture, TranscriptSegment, new_id
    from checkpoint.services import automatic
    from checkpoint.services.inbox import reconcile
    from checkpoint.services.review import list_work_items
    cid, sid, source = auto_observation(app_env)
    judge = Mock(return_value=False)
    monkeypatch.setattr(automatic, 'confirm', judge)
    reconcile()
    automatic.settle()
    assert list_work_items() == []
    judge.assert_not_called()
    with write_session() as s:
        s.add(TranscriptSegment(id=new_id(), session_id=sid, source_id=source, start_ms=26000, end_ms=28000,
                                text='Actually that is expected behaviour, leave it alone'))
    automatic.settle()
    assert 'expected behaviour' in judge.call_args.args[1]
    assert not list_work_items()
    with read_session() as s:
        assert s.get(Capture, cid).status == 'discarded'


def test_automatic_repeats_bundle_and_explicit_feedback_is_session_scoped(app_env, monkeypatch):
    from checkpoint.db import write_session
    from checkpoint.models import Capture, utcnow
    from checkpoint.services import automatic
    from checkpoint.services.task_evidence import feedback
    from checkpoint.services.review import get_work_item, list_work_items, update_work_item
    cid, sid, _ = auto_observation(app_env)
    with write_session() as s:
        s.get(Capture, cid).created_at = utcnow() - timedelta(seconds=61)
    monkeypatch.setattr(automatic, 'confirm', lambda *args: True)
    automatic.settle()
    iid = list_work_items()[0]['id']
    second, _, _ = auto_observation(app_env, at=70000)
    with write_session() as s:
        s.get(Capture, second).created_at = utcnow() - timedelta(seconds=61)
    automatic.settle()
    assert len(list_work_items()) == 1 and get_work_item(iid)['capture_count'] == 2
    item = get_work_item(iid)
    update_work_item(iid, {'work_status': 'deleted'})
    assert not automatic.suppressed(item['project_id'], sid, 'The menu overlaps the controls')
    update_work_item(iid, {'work_status': 'open'})
    feedback(iid, 'expected')
    assert automatic.suppressed(item['project_id'], sid, 'The menu overlaps the controls')
    assert not automatic.suppressed(item['project_id'], 'next-session', 'The menu overlaps the controls')
    assert not automatic.suppressed(item['project_id'], sid, 'The score resets in level 2')
    update_work_item(iid, {'work_status': 'open'})
    assert not automatic.suppressed(item['project_id'], sid, 'The menu overlaps the controls')


def test_model_failure_keeps_automatic_evidence_for_retry(app_env, monkeypatch):
    from checkpoint.db import read_session, write_session
    from checkpoint.models import Capture, utcnow
    from checkpoint.services import automatic
    from checkpoint.services.review import list_work_items
    cid, _, _ = auto_observation(app_env)
    with write_session() as s:
        s.get(Capture, cid).created_at = utcnow() - timedelta(seconds=61)
    monkeypatch.setattr(automatic, 'confirm', Mock(side_effect=RuntimeError('offline')))
    automatic.settle()
    with read_session() as s:
        assert s.get(Capture, cid).status == 'marker'
    assert not list_work_items()


def test_word_transcription_forms_a_sentence_for_detection():
    from checkpoint.capture.auto import utterances
    words = [SimpleNamespace(id=str(index), start_ms=index * 300, end_ms=index * 300 + 200,
        text=word, corrected_text=None, low_confidence=False, chunk_id='one')
        for index, word in enumerate('The button is broken'.split())]
    rows = utterances(words)
    assert len(rows) == 1 and rows[0][3] == 'The button is broken' and not rows[0][4]


def test_note_editor_reopens_existing_text_and_keeps_edits_on_close(app_env):
    from test_native_desktop import app
    from checkpoint.desktop.windows import NoteWindow
    from checkpoint.services.task_evidence import capture_editor_payload
    from checkpoint.services.review import update_work_item, get_work_item
    application = app()
    service, cid, _ = captured(app_env)
    service.save_capture_note(cid, 'Original note')
    payload = capture_editor_payload(cid)
    def save(data, text, category):
        update_work_item(data['item_id'], {'description': text})
    win = NoteWindow(save, Mock(), Mock())
    win.open_for(payload)
    assert win.text.toPlainText() == 'Original note'
    win.text.setPlainText('Changed note')
    win._keep()
    win.open_for(payload)
    assert win.text.toPlainText() == 'Changed note'
    assert get_work_item(payload['item_id'])['description'] == 'Changed note'
    win._close()


def test_uncertain_word_highlights_and_selection_plays_short_excerpt(app_env):
    from test_native_desktop import app
    from checkpoint.desktop.windows import NoteWindow
    from checkpoint.services.voice import retain, finish_note
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.task_evidence import capture_editor_payload
    from PySide6.QtGui import QTextCursor
    application = app()
    service, cid, _ = captured(app_env)
    service.save_capture_note(cid, 'Menu overlaps')
    nid = retain(cid, wav_bytes(4))
    finish_note(nid, 'Menu overlaps', [{'text': 'Menu', 'start': 1, 'end': 1.2, 'low': True}])
    win = NoteWindow(Mock(), Mock(), Mock())
    win.open_for(capture_editor_payload(cid))
    assert win.play_btn.isVisible() and len(win.text.extraSelections()) == 1
    cursor = win.text.textCursor()
    cursor.setPosition(0); cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    win.text.setTextCursor(cursor)
    assert win.playback.entries[0]['start_s'] == 1
    win._close()


def test_hold_captures_initial_frame_and_saves_on_release(app_env, monkeypatch):
    from test_native_desktop import app
    from checkpoint.desktop import host as desktop, windows
    from checkpoint.audio import dictation
    from checkpoint.capture import screen
    from checkpoint.services.capture import CaptureService, default_project_id
    from checkpoint.services.sessions import SessionManager
    from checkpoint.services.review import list_work_items, get_work_item
    from checkpoint.settings_store import update_settings
    application = app()
    pid = default_project_id()
    update_settings({'audio': {'mic_enabled': True}, 'last_project_id': pid})
    sessions = SessionManager()
    service = CaptureService(sessions)
    core = SimpleNamespace(capture=service, sessions=sessions, inbox=Mock(),
                           worker_status=lambda: {'transcription': {}}, appwatch=Mock())
    monkeypatch.setattr(desktop, 'get_core', lambda: core)
    png, thumb = png_bytes()
    original = screen.Grab(png, thumb, 320, 180, {}, 'Game', 0, None, False)
    grab = Mock(return_value=original)
    monkeypatch.setattr(screen, 'grab', grab)
    recording = SimpleNamespace(audio=b'', phase='recording', text='', words=[], error='', retained_note_id=None,
        start=Mock(), stop=Mock(), cancel=Mock())
    monkeypatch.setattr(dictation, 'NoteDictation', lambda device: recording)
    host = desktop.DesktopHost(show_panel=False)
    host._on_hotkey('press')
    recording.start.assert_called_once()
    host._on_hotkey('hold')
    application.processEvents()
    assert len(list_work_items()) == 1 and len(host._held_jobs) == 1
    host._on_hotkey('release')
    recording.stop.assert_called_once()
    recording.audio, recording.phase = wav_bytes(), 'transcribing'
    host._poll_held_notes()
    assert recording.retained_note_id
    from checkpoint.services.task_evidence import capture_editor_payload
    editing_payload = capture_editor_payload(next(e['capture_id'] for e in get_work_item(list_work_items()[0]['id'])['evidence'] if e['kind'] == 'capture'))
    host.note_window.open_for(editing_payload)
    host.note_window.text.setPlainText('Typed during transcription')
    recording.text, recording.phase = 'The menu overlaps', 'done'
    host._poll_held_notes()
    item = get_work_item(list_work_items()[0]['id'])
    assert item['description'] == 'The menu overlaps'
    assert any(e.get('audio_url') for e in item['evidence'])
    assert host.note_window.text.toPlainText() == 'Typed during transcription\n\nThe menu overlaps'
    host.note_window._keep()
    assert get_work_item(item['id'])['description'] == 'Typed during transcription\n\nThe menu overlaps'
    grab.assert_called_once()  # The screenshot came from the initial press.
    host.overlay.hide(); host.note_window.hide(); host.toast.hide()


def test_cancelled_snip_adds_no_evidence(app_env):
    from test_native_desktop import app
    from checkpoint.desktop.snipping import SnippingOverlay
    from checkpoint.capture.screen import Grab
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    application = app()
    png, thumb = png_bytes()
    grab = Grab(png, thumb, 320, 180, {'region': {'left': 0, 'top': 0, 'width': 320, 'height': 180}}, '', 0, None, False)
    done = Mock()
    overlay = SnippingOverlay(grab, done)
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    done.assert_called_once_with(None)
    assert not overlay.isVisible()


def test_composite_keeps_large_screenshot_pixels(tmp_path):
    from checkpoint.services.image_cards import render_cards
    path = tmp_path / 'wide.png'
    Image.new('RGB', (2400, 1400), '#123456').save(path)
    item = {'title': 'Large screenshot', 'description': 'Full detail', 'excerpts': []}
    with Image.open(io.BytesIO(render_cards(item, path)[0])) as image:
        assert image.width == 2400 and image.getpixel((2399, 1463)) == (18, 52, 86)


def test_ended_session_waits_for_pending_context_transcription(app_env, monkeypatch):
    from checkpoint.db import write_session
    from checkpoint.models import AudioChunk, Session, TranscriptionJob, Capture, utcnow
    from checkpoint.services import automatic
    cid, sid, source_id = auto_observation(app_env)
    with write_session() as s:
        s.get(Session, sid).state = 'ended'
        s.get(Capture, cid).created_at = utcnow() - timedelta(seconds=61)
        chunk = AudioChunk(session_id=sid, source_id=source_id, seq=1, rel_path=f'{sid}/pending.wav',
                           start_offset_ms=20000, duration_ms=20000, sample_rate=16000, channels=1, state='complete')
        s.add(chunk); s.flush()
        s.add(TranscriptionJob(session_id=sid, chunk_id=chunk.id, state='queued'))
    judge = Mock(return_value=True)
    monkeypatch.setattr(automatic, 'confirm', judge)
    automatic.settle()
    judge.assert_not_called()
