"""Retention removes media copies, never the user's written history."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PIL import Image

from checkpoint.db import read_session, write_session
from checkpoint.models import (AudioChunk, Capture, DraftItem, EvidenceLink, Note,
                               Session, TranscriptSegment, TranscriptionJob, WorkItem, utcnow)
from checkpoint.services import image_cards, retention, review
from checkpoint.storage import atomic_write_bytes, audio_path, media_path
from conftest import restart
from test_simple_inbox import captured


def task(app_env, status='deleted', age=2, session_id=None):
    service, cid, _ = captured(app_env, session_id)
    wid = service.save_capture_note(cid, 'Keep this original note')['work_item_id']
    review.update_work_item(wid, {'work_status': status})
    with write_session() as s:
        w = s.get(WorkItem, wid)
        w.status_changed_at = utcnow() - timedelta(days=age)
        c = s.get(Capture, cid)
        files = (media_path(c.image_rel_path), media_path(c.thumb_rel_path))
    return wid, cid, files


@pytest.mark.parametrize('status,days', [('deleted', 1), ('done', 7), ('wont_do', 7)])
def test_default_cutoffs_and_text_survive_restart(app_env, monkeypatch, status, days):
    now = utcnow()
    monkeypatch.setattr(retention, 'utcnow', lambda: now)
    old, cid, files = task(app_env, status, days + 1)
    young, _, young_files = task(app_env, status, 0)
    open_id, _, open_files = task(app_env, 'open', 90)
    with write_session() as s:
        s.get(WorkItem, old).status_changed_at = now - timedelta(days=days)
        human_time = s.get(WorkItem, old).human_updated_at
    assert retention.cleanup_item_media()['items'] == 1
    assert all(not p.exists() for p in files)
    assert all(p.exists() for p in (*young_files, *open_files))
    restart()
    item = review.get_work_item(old)
    assert item['description'] == 'Keep this original note'
    assert any(e.get('text') == 'Keep this original note' for e in item['evidence'])
    assert item['human_updated_at'] == human_time.isoformat()
    shot = next(e for e in item['evidence'] if e['kind'] == 'capture')
    assert shot['media_removed'] and shot['image_url'] == retention.placeholder_url(status)
    assert review.get_work_item(young)['media_removed_at'] is None
    assert review.get_work_item(open_id)['media_removed_at'] is None
    assert retention.cleanup_item_media()['items'] == 0
    # Undo/reopen keeps the text and truthfully shows that the media is gone.
    assert review.update_work_item(old, {'work_status': 'open'})['media_removed_at']
    with read_session() as s:
        assert s.get(Capture, cid).media_removed_at


def test_edits_originals_thumbnails_ai_and_export_copies_are_retired(app_env):
    from checkpoint.config import paths
    from checkpoint.services.cropping import crop
    from checkpoint.services.handoff import build
    wid, cid, files = task(app_env)
    crop(cid, (10, 10, 100, 80))
    crop(cid, (20, 10, 80, 60))
    ai_copy = build([wid], True, True).images[0][1]
    card_root = image_cards.refresh()
    export_folder = image_cards.write_folder([wid])
    legacy = paths().exports / 'cards'
    legacy.mkdir(parents=True)
    source = next(card_root.rglob(f'{wid}-*.png'))
    legacy_copy = legacy / source.name
    legacy_copy.write_bytes(source.read_bytes())
    unrelated = files[0].parent / 'user-image.png'
    unrelated.write_bytes(files[0].read_bytes())
    voice = b'recorded voice bytes'
    from checkpoint.services.voice import retain
    nid = retain(cid, voice, 'Original dictated words')
    with read_session() as s:
        voice_path = audio_path(s.get(Note, nid).audio_rel_path)
    retention.cleanup_item_media()
    assert not list(files[0].parent.glob(f'{cid}*'))
    assert not ai_copy.exists() and not voice_path.exists()
    assert unrelated.exists()
    for folder in (card_root, export_folder, legacy):
        for path in folder.rglob(f'{wid}-*.png'):
            with Image.open(path) as image:
                assert 'Keep this original note' in image.info['Checkpoint.Context']
                assert image.info['Checkpoint.Item'].find('media_removed_at') >= 0
    with read_session() as s:
        assert s.get(Note, nid).text == 'Original dictated words'
        assert s.get(Note, nid).audio_rel_path is None
    assert not build([wid], True, True).images


def test_shared_capture_and_voice_wait_for_last_owner(app_env):
    from checkpoint.services.voice import retain
    wid, cid, files = task(app_env)
    nid = retain(cid, b'voice', 'Shared speech')
    other = review.create_work_item(review.get_work_item(wid)['project_id'], 'task', 'Still open')['id']
    with write_session() as s:
        s.add(EvidenceLink(work_item_id=other, capture_id=cid))
        voice_path = audio_path(s.get(Note, nid).audio_rel_path)
    retention.cleanup_item_media()
    assert all(p.exists() for p in files) and voice_path.exists()
    assert review.get_work_item(wid)['media_removed_at']
    assert not review.get_work_item(other)['media_removed_at']
    review.update_work_item(other, {'work_status': 'done'})
    with write_session() as s:
        s.get(WorkItem, other).status_changed_at = utcnow() - timedelta(days=8)
    retention.cleanup_item_media()
    assert all(not p.exists() for p in files) and not voice_path.exists()


def test_pending_draft_protects_attachments(app_env):
    wid, cid, files = task(app_env)
    with write_session() as s:
        draft = DraftItem(project_id=s.get(WorkItem, wid).project_id, title='Pending review', review_state='pending')
        s.add(draft); s.flush()
        s.add(EvidenceLink(draft_id=draft.id, capture_id=cid))
    retention.cleanup_item_media()
    assert all(p.exists() for p in files)


def test_recordings_preserve_transcripts_and_protect_shared_chunks(app_env):
    from test_session_transcripts import recording
    sid, chunks, jobs, sources = recording(state='ended')
    wid, _, _ = task(app_env, session_id=sid)
    other = review.create_work_item(review.get_work_item(wid)['project_id'], 'task', 'Open recording', session_id=sid)['id']
    with write_session() as s:
        for index, (chunk_id, source_id, job_id) in enumerate(zip(chunks, sources, jobs)):
            s.get(TranscriptionJob, job_id).state = 'done'
            chunk = s.get(AudioChunk, chunk_id)
            atomic_write_bytes(audio_path(chunk.rel_path), b'audio')
            seg = TranscriptSegment(id=f'segment-{index}', session_id=sid, source_id=source_id,
                                    chunk_id=chunk_id, start_ms=index * 10000, end_ms=index * 10000 + 900,
                                    text=f'Original transcript {index}', corrected_text=f'Corrected transcript {index}')
            s.add(seg); s.flush()
            s.add(EvidenceLink(work_item_id=wid, segment_id=seg.id))
        s.add(EvidenceLink(work_item_id=other, segment_id='segment-0'))
        files = [audio_path(s.get(AudioChunk, cid).rel_path) for cid in chunks]
    retention.cleanup_item_media()
    assert files[0].exists() and not files[1].exists()
    with read_session() as s:
        assert s.get(TranscriptSegment, 'segment-1').text == 'Original transcript 1'
        assert s.get(TranscriptSegment, 'segment-1').corrected_text == 'Corrected transcript 1'
        assert not s.get(Session, sid).raw_audio_deleted
    review.update_work_item(other, {'work_status': 'deleted'})
    with write_session() as s:
        s.get(WorkItem, other).status_changed_at = utcnow() - timedelta(days=2)
    retention.cleanup_item_media()
    assert not files[0].exists()
    with read_session() as s:
        assert s.get(Session, sid).raw_audio_deleted


@pytest.mark.parametrize('session_state,job_state', [('active', 'done'), ('ended', 'running'), ('ended', 'queued'), ('ended', 'failed')])
def test_recordings_in_use_are_protected_but_backlog_does_not_block_cleanup(app_env, session_state, job_state):
    from test_session_transcripts import recording
    sid, chunks, jobs, _ = recording(state=session_state)
    task(app_env, session_id=sid)
    with write_session() as s:
        files = []
        for cid, jid in zip(chunks, jobs):
            s.get(TranscriptionJob, jid).state = job_state
            path = audio_path(s.get(AudioChunk, cid).rel_path)
            atomic_write_bytes(path, b'audio awaiting transcription')
            files.append(path)
    retention.cleanup_item_media()
    protected = session_state == 'active' or job_state == 'running'
    assert all(p.exists() == protected for p in files)
    if not protected:
        with read_session() as s:
            assert all(s.get(TranscriptionJob, jid).state == 'failed' for jid in jobs)


def test_failed_removal_retries_without_losing_text(app_env, monkeypatch):
    wid, cid, files = task(app_env)
    unlink = retention._unlink
    def locked(path, base):
        if path == files[0]:
            raise PermissionError('File in use')
        unlink(path, base)
    monkeypatch.setattr(retention, '_unlink', locked)
    retention.cleanup_item_media()
    assert files[0].exists()
    assert review.get_work_item(wid)['description'] == 'Keep this original note'
    with read_session() as s:
        assert s.get(Capture, cid).media_removed_at is None
    monkeypatch.setattr(retention, '_unlink', unlink)
    retention.cleanup_item_media()
    assert all(not p.exists() for p in files)


def test_settings_defaults_custom_values_disabled_and_status_clocks(app_env):
    from checkpoint.settings_store import get_settings, update_settings
    from pydantic import ValidationError
    assert get_settings().retention.model_dump() == {'deleted_days': 1, 'done_days': 7}
    wid, _, files = task(app_env, 'done', 20)
    update_settings({'retention': {'done_days': None}})
    retention.cleanup_item_media()
    assert all(p.exists() for p in files)
    update_settings({'retention': {'done_days': 30, 'deleted_days': 3}})
    restart()
    assert get_settings().retention.done_days == 30
    with pytest.raises(ValidationError):
        update_settings({'retention': {'done_days': -1}})
    old = review.get_work_item(wid)['status_changed_at']
    assert review.update_work_item(wid, {'description': 'Edited', 'work_status': 'done'})['status_changed_at'] == old
    review.update_work_item(wid, {'work_status': 'open'})
    retention.cleanup_item_media()
    assert all(p.exists() for p in files)
    assert review.update_work_item(wid, {'work_status': 'done'})['status_changed_at'] != old


def test_startup_sweeps_old_items_without_workers(app_env):
    from checkpoint.core import AppCore
    wid, _, files = task(app_env)
    instance = AppCore.__new__(AppCore)
    instance.sessions = SimpleNamespace(recover_on_launch=lambda: [])
    instance.start(workers=False)
    assert instance.started and review.get_work_item(wid)['media_removed_at']
    assert all(not p.exists() for p in files)


def test_glass_pngs_have_transparency_and_distinct_status_colors(app_env):
    for status, channel in (('done', 1), ('deleted', 0)):
        with Image.open(retention.placeholder_path(status)) as image:
            assert image.mode == 'RGBA' and image.size == (1600, 1000)
            assert image.getpixel((0, 0))[3] == 0
            color = image.getpixel((90, 180))
            assert 0 < color[3] < 255 and color[channel] > color[2]


def test_existing_database_backfills_completion_and_deletion_ages(app_env):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from checkpoint import db
    from checkpoint.settings_store import get_settings, invalidate_cache
    from checkpoint.models import Setting
    done, _, done_files = task(app_env, 'done', 30)
    deleted, _, deleted_files = task(app_env, 'deleted', 30)
    with write_session() as s:
        s.get(WorkItem, done).completed_at = utcnow() - timedelta(days=8)
        s.get(WorkItem, deleted).human_updated_at = utcnow() - timedelta(days=2)
        # A real pre-feature preference record has no retention section.
        s.get(Setting, 'app').value = {'version': 6, 'first_run_complete': True}
    cfg = Config()
    cfg.set_main_option('script_location', str(db.MIGRATIONS_DIR))
    with db.engine().begin() as connection:
        cfg.attributes['connection'] = connection
        command.downgrade(cfg, '0006')
        assert 'media_removed_at' not in {r[1] for r in connection.execute(text('PRAGMA table_info(work_items)'))}
    restart()
    invalidate_cache()
    assert get_settings().retention.deleted_days == 1
    assert get_settings().retention.done_days == 7
    retention.cleanup_item_media()
    assert all(not p.exists() for p in (*done_files, *deleted_files))
    assert review.get_work_item(done)['description'] == 'Keep this original note'


def test_native_settings_save_and_retired_task_preview(app_env, monkeypatch):
    from test_native_desktop import app, settings_dialog
    from checkpoint.desktop.editor import TaskDialog
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.settings_store import get_settings
    application = app()
    dialog = settings_dialog(monkeypatch)
    assert dialog.deleted_days.value() == 1 and dialog.done_days.value() == 7
    dialog.deleted_days.setValue(3)
    dialog.done_days.setValue(0)
    dialog._save()
    assert get_settings().retention.deleted_days == 3
    assert get_settings().retention.done_days is None
    dialog.close()
    wid, _, _ = task(app_env, age=4)
    retention.cleanup_item_media()
    item = review.get_work_item(wid)
    panel = InboxPanel()
    assert not panel._pixmap(item).isNull()
    editor = TaskDialog(wid)
    assert editor.description.toPlainText() == 'Keep this original note'
    assert not editor.workspaces and editor.image_tabs.count() == 0
    assert editor.images.currentWidget() is editor.empty_image
    assert 'No image attached' in editor.empty_image.text()
    assert editor.add_image_btn.isEnabled()
    editor.close()
    panel.close()


def test_reopened_text_only_task_can_accept_a_new_screenshot(app_env):
    from conftest import png_bytes
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager
    from checkpoint.services.task_evidence import last_editor_payload
    wid, old_cid, _ = task(app_env)
    retention.cleanup_item_media()
    item = review.update_work_item(wid, {'work_status': 'open'})
    assert last_editor_payload(item['project_id']).get('id') is None
    png, thumb = png_bytes()
    service = CaptureService(SessionManager())
    new_cid = service.import_image(png, thumb, 320, 180, project_id=item['project_id'], session_id=None,
                                   offset_ms=None, taken_at=utcnow(), status='saved', trigger='snip', attach_to=wid)
    current = review.get_work_item(wid)
    assert current['media_removed_at'] is None
    assert next(e for e in current['evidence'] if e.get('capture_id') == old_cid)['media_removed']
    assert not next(e for e in current['evidence'] if e.get('capture_id') == new_cid).get('media_removed')
    assert len(image_cards._sources(current, True)) == 1


def test_capture_urls_return_status_placeholder_and_block_restoration(app_env):
    from checkpoint.api.routes import media, retention_placeholder
    from checkpoint.services.cropping import crop
    wid, cid, _ = task(app_env)
    retention.cleanup_item_media()
    for variant in ('image', 'thumb', 'original'):
        assert media(cid, variant).path == retention.placeholder_path('deleted')
    assert retention_placeholder('done').path == retention.placeholder_path('done')
    with pytest.raises(ValueError):
        crop(cid, None)
    assert review.get_work_item(wid)['description'] == 'Keep this original note'
