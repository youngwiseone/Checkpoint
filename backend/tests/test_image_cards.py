import io
import json

from PIL import Image

from checkpoint.services.image_cards import _font, _wrap, context_text, render_cards


def test_long_unicode_context_visible_and_lossless(tmp_path):
    text = ('Long note with Māori macrons āēīōū and emoji 🎮. ' * 220) + '\nLAST LINE stays visible.'
    item = {'id': 'test', 'title': 'Clean card', 'description': text, 'project_name': 'Testing', 'work_status': 'open', 'excerpts': []}
    shot = tmp_path / 'shot.png'
    Image.new('RGB', (500, 300), '#234567').save(shot)
    cards = render_cards(item, shot)
    assert len(cards) == 1
    for number, data in enumerate(cards, 1):
        with Image.open(io.BytesIO(data)) as image:
            assert image.info['Checkpoint.Context'] == context_text(item)
            metadata = json.loads(image.info['Checkpoint.Item'])
            assert metadata['page'] == number and metadata['pages'] == len(cards)
            assert metadata['item']['description'] == text
            assert image.height > 64 + 300 + 195 + 24 * 34
    wrapped = _wrap(context_text(item), _font(26), 1120)
    assert ''.join(wrapped).replace('\n', '') == context_text(item).replace('\n', '')
    assert wrapped[-1] == 'LAST LINE stays visible.'


def test_text_only_card():
    item = {'id': 'test', 'title': 'Text only', 'description': 'No screenshot required.', 'excerpts': []}
    cards = render_cards(item)
    with Image.open(io.BytesIO(cards[0])) as image:
        assert 'No screenshot required.' in image.info['Checkpoint.Context']
        assert image.width == 1200


def test_note_edits_render_once_without_title_or_history(app_env):
    from checkpoint.services import image_cards, review
    from test_simple_inbox import captured

    service, cid, _ = captured(app_env)
    original = 'Original note that becomes a title'
    iid = service.save_capture_note(cid, original)['work_item_id']
    root = image_cards.refresh()
    target = next(root.rglob(f'{iid}-*.png'))
    with Image.open(target) as image:
        assert image.info['Checkpoint.Context'].count(original) == 1
        screenshot = image.crop((440, 64, 760, 244)).tobytes()

    for note in ('Updated short note', 'A long updated note. ' * 40 + '\nLast line.', ''):
        review.update_work_item(iid, {'description': note})
        image_cards.refresh()
        assert list(root.rglob(f'{iid}-*.png')) == [target]
        with Image.open(target) as image:
            context = image.info['Checkpoint.Context']
            assert original not in context
            assert context == 'Project: General | Status: open' + ('\n\n' + note if note else '')
            assert json.loads(image.info['Checkpoint.Item'])['item']['description'] == note
            assert image.crop((440, 64, 760, 244)).tobytes() == screenshot


def test_distinct_title_is_not_rendered():
    item = {'title': 'An obsolete title', 'description': 'The current note', 'excerpts': []}
    with Image.open(io.BytesIO(render_cards(item)[0])) as image:
        assert 'An obsolete title' not in image.info['Checkpoint.Context']
        assert image.info['Checkpoint.Context'].count('The current note') == 1


def test_live_refresh_updates_and_removes_deleted(app_env):
    from checkpoint.db import write_session
    from checkpoint.models import Project, WorkItem
    from checkpoint.services import image_cards, review
    with write_session() as session:
        project = Project(name='PNG tests')
        session.add(project)
        session.flush()
        item = WorkItem(project_id=project.id, type='note', title='Original', description='Initial text')
        session.add(item)
        session.flush()
        iid = item.id
    folder = image_cards.refresh()
    files = list(folder.rglob('*.png'))
    assert len(files) == 1
    review.update_work_item(iid, {'description': 'Updated text'})
    image_cards.refresh()
    with Image.open(files[0]) as image:
        assert 'Updated text' in image.info['Checkpoint.Context']
        assert 'Initial text' not in image.info['Checkpoint.Context']
    review.update_work_item(iid, {'work_status': 'deleted'})
    image_cards.refresh()
    assert not list(files[0].parent.glob('*.png'))
    assert len(list(folder.rglob('Deleted/*.png'))) == 1



def test_missing_continuation_page_is_restored(app_env):
    from checkpoint.db import write_session
    from checkpoint.models import Project, WorkItem
    from checkpoint.services import image_cards
    with write_session() as session:
        project = Project(name='Continuation')
        session.add(project)
        session.flush()
        item = WorkItem(project_id=project.id, type='note', title='Long', description='Long line of notes. ' * 500)
        session.add(item)
    folder = image_cards.refresh()
    files = sorted(folder.rglob('*.png'))
    assert len(files) == 1
    missing = files[-1]
    original = missing.read_bytes()
    missing.unlink()
    image_cards.refresh()
    assert missing.read_bytes() == original


def test_crop_and_corrected_speech_refresh_png(app_env):
    from checkpoint.db import read_session, write_session
    from checkpoint.models import TranscriptSegment
    from checkpoint.services import cropping, demo, image_cards, review
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager
    demo.create_demo(CaptureService(SessionManager()))
    approved = review.approve([item['id'] for item in review.list_drafts(state='pending')])
    items = [review.get_work_item(item['work_item_id']) for item in approved]
    item = next(item for item in items if any(e['kind'] == 'capture' for e in item['evidence']) and any(e['kind'] == 'segment' for e in item['evidence']))
    capture = next(e for e in item['evidence'] if e['kind'] == 'capture')
    segment = next(e for e in item['evidence'] if e['kind'] == 'segment')
    folder = image_cards.refresh()
    target = sorted(folder.rglob(f"{item['id']}-*.png"))[0]
    original = target.read_bytes()
    cropping.crop(capture['capture_id'], (0, 0, 100, 80))
    with write_session() as session:
        speech = session.get(TranscriptSegment, segment['segment_id'])
        speech.corrected_text = 'Corrected speech included in PNG.'
    image_cards.refresh()
    assert target.read_bytes() != original
    with Image.open(target) as image:
        assert 'Corrected speech included in PNG.' in image.info['Checkpoint.Context']
        assert image.getpixel((550, 64)) != (255, 255, 255)
    cropped = target.read_bytes()
    cropping.crop(capture['capture_id'], None)
    image_cards.refresh()
    assert target.read_bytes() != cropped


def test_project_status_moves_and_legacy_cleanup(app_env):
    from checkpoint.db import write_session
    from checkpoint.models import Project, WorkItem
    from checkpoint.services import image_cards, review
    from checkpoint.config import paths
    with write_session() as session:
        project = Project(name='../Unsafe: Project')
        session.add(project)
        session.flush()
        pid = project.id
        item = WorkItem(project_id=pid, type='note', title='Move me', description='Latest note')
        session.add(item)
        session.flush()
        iid = item.id
    root = image_cards.refresh()
    opened = image_cards.output_folder(pid)
    source = next(opened.glob('*.png'))
    filename = source.name
    legacy = paths().exports / 'cards'
    legacy.mkdir(parents=True)
    (legacy / filename).write_bytes(source.read_bytes())
    (legacy / 'personal.png').write_bytes(b'Keep user file')
    for status, name in [('done', 'Done'), ('deleted', 'Deleted'), ('open', 'Open')]:
        review.update_work_item(iid, {'work_status': status})
        image_cards.refresh()
        target = image_cards.output_folder(pid, status) / filename
        assert target.is_file() and target.parent.name == name
        assert len(list(root.rglob(filename))) == 1
    assert not (legacy / filename).exists()
    assert (legacy / 'personal.png').read_bytes() == b'Keep user file'
    assert opened.resolve().is_relative_to(root.resolve())


def test_edited_note_replaces_history_in_pixels_and_metadata(app_env):
    from checkpoint.db import write_session
    from checkpoint.models import EvidenceLink, Note, Project, WorkItem
    from checkpoint.services import image_cards, review
    with write_session() as session:
        project = Project(name='Edit')
        session.add(project)
        session.flush()
        item = WorkItem(project_id=project.id, type='note', title='Edit note', description='Old text')
        note = Note(project_id=project.id, text='Old text')
        session.add_all([item, note])
        session.flush()
        iid = item.id
        session.add(EvidenceLink(work_item_id=iid, note_id=note.id, confidence='direct', attached_by='user'))
    for new_text in ('Completely new text', ''):
        review.update_work_item(iid, {'description': new_text})
        selected = review.get_work_item(iid)
        assert 'Old text' not in review.visible_context(selected)
        folder = image_cards.refresh()
        with Image.open(next(folder.rglob('*.png'))) as image:
            assert 'Old text' not in image.info['Checkpoint.Context']
            assert 'Old text' not in image.info['Checkpoint.Item']
            assert new_text in image.info['Checkpoint.Context']


def test_background_changes_do_not_shuffle_human_order(app_env):
    from datetime import datetime, timedelta, timezone
    from checkpoint.db import write_session
    from checkpoint.models import Project, WorkItem
    from checkpoint.services import review
    now = datetime.now(timezone.utc)
    with write_session() as session:
        project = Project(name='Chronology')
        session.add(project)
        session.flush()
        pid = project.id
        older = WorkItem(project_id=pid, type='note', title='Older', human_updated_at=now - timedelta(days=1))
        newer = WorkItem(project_id=pid, type='note', title='Newer', human_updated_at=now)
        session.add_all([older, newer])
        session.flush()
        older_id, newer_id = older.id, newer.id
    with write_session() as session:
        item = session.get(WorkItem, older_id)
        item.description = 'Background transcription'
        item.updated_at = now + timedelta(days=1)
    assert [i['id'] for i in review.list_work_items(pid)] == [newer_id, older_id]
    review.update_work_item(older_id, {'work_status': 'done'})
    assert review.list_work_items(pid)[0]['id'] == older_id
    assert len(review.list_work_items(pid, session_id='__unrecorded__')) == 2
    assert not review.list_work_items(pid, session_id='missing-session')


def test_existing_0004_database_migrates_human_timestamp(app_env):
    from datetime import datetime, timezone
    from alembic import command
    from alembic.config import Config
    from checkpoint import db
    from checkpoint.models import Project, WorkItem
    known = datetime(2025, 5, 4, 3, 2, 1, tzinfo=timezone.utc)
    with db.write_session() as session:
        project = Project(name='Existing')
        session.add(project)
        session.flush()
        item = WorkItem(project_id=project.id, type='note', title='Survives migration', description='Existing text', updated_at=known)
        session.add(item)
        session.flush()
        iid = item.id
    cfg = Config()
    cfg.set_main_option('script_location', str(db.MIGRATIONS_DIR))
    with db.engine().begin() as connection:
        cfg.attributes['connection'] = connection
        command.downgrade(cfg, '0004')
    db.migrate()
    with db.read_session() as session:
        item = session.get(WorkItem, iid)
        assert item.title == 'Survives migration' and item.description == 'Existing text'
        assert item.human_updated_at == known
        assert item.description_edited is False
