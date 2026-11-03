"""Selected task merges preserve content, order and ownership across restarts."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import restart
from test_note_workflow import wav_bytes
from test_simple_inbox import captured


def task(app_env, text):
    service, cid, _ = captured(app_env)
    iid = service.save_capture_note(cid, text)['work_item_id']
    return iid, cid


def test_merge_only_selected_tasks_in_creation_order_with_all_media(app_env):
    from checkpoint.db import read_session, write_session
    from checkpoint.models import Capture, DraftItem, EvidenceLink, WorkItem, utcnow
    from checkpoint.services.inbox import ensure_capture, reconcile
    from checkpoint.services.review import get_work_item, list_work_items
    from checkpoint.services.task_evidence import merge_work_items
    from checkpoint.services.voice import retain, finish_note, evidence_audio

    first, c1 = task(app_env, 'First note has more detail')
    untouched, _ = task(app_env, 'Leave this task alone')
    middle, c2 = task(app_env, 'First note')  # A substring must still be appended.
    last, c3 = task(app_env, 'Last note')
    nid = retain(c2, wav_bytes())
    finish_note(nid, 'Recorded words', [])
    with write_session() as s:
        base = utcnow() - timedelta(days=1)
        for index, iid in enumerate((first, untouched, middle, last)):
            w = s.get(WorkItem, iid)
            w.created_at = base + timedelta(minutes=index)
            w.human_updated_at = base + timedelta(minutes=10 - index)
        # These offsets would incorrectly sort the last screenshot first.
        for cid, offset in ((c1, 90000), (c2, None), (c3, 1000)):
            s.get(Capture, cid).offset_ms = offset
        draft = DraftItem(project_id=s.get(WorkItem, middle).project_id, work_item_id=middle,
                          type='note', title='History', review_state='approved')
        s.add(draft)
        s.flush()
        draft_id = draft.id
        # A shared evidence source must survive once, without losing its audio.
        s.add(EvidenceLink(work_item_id=last, note_id=nid))
    before = get_work_item(untouched)
    versions = {iid: get_work_item(iid)['version'] for iid in (first, middle, last)}
    assert merge_work_items([last, first, middle], versions) == first
    reconcile()
    restart()
    merged = get_work_item(first)
    assert merged['description'] == 'First note has more detail\n\nFirst note\n\nLast note'
    assert [e['capture_id'] for e in merged['evidence'] if e['kind'] == 'capture'] == [c1, c2, c3]
    voice = [e for e in merged['evidence'] if e.get('note_id') == nid]
    assert len(voice) == 1 and evidence_audio(voice[0]) == wav_bytes()
    assert {item['id'] for item in list_work_items()} == {first, untouched}
    assert get_work_item(untouched) == before
    assert all(ensure_capture(cid) == first for cid in (c1, c2, c3))
    with read_session() as s:
        assert s.get(DraftItem, draft_id).work_item_id == first
        assert s.get(WorkItem, middle).duplicate_of_id == first
        assert s.get(WorkItem, last).duplicate_of_id == first
        links = s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == first)).all()
        assert len({link.work_item_order for link in links}) == len(links)


@pytest.mark.parametrize('invalid', ['stale', 'missing', 'project', 'merged', 'one'])
def test_invalid_merge_changes_nothing(app_env, invalid):
    from checkpoint.db import write_session
    from checkpoint.models import Project, WorkItem
    from checkpoint.services.review import get_work_item
    from checkpoint.services.task_evidence import merge_work_items
    first, _ = task(app_env, 'First')
    second, _ = task(app_env, 'Second')
    versions = {iid: get_work_item(iid)['version'] for iid in (first, second)}
    ids = [first, second]
    if invalid == 'stale':
        versions[second] -= 1
    elif invalid == 'missing':
        ids.append('missing')
    elif invalid == 'project':
        with write_session() as s:
            project = Project(name='Other project')
            s.add(project)
            s.flush()
            s.get(WorkItem, second).project_id = project.id
    elif invalid == 'merged':
        with write_session() as s:
            s.get(WorkItem, second).duplicate_of_id = first
    else:
        ids = [first, first]
    before = [get_work_item(iid) for iid in (first, second)]
    with pytest.raises(ValueError):
        merge_work_items(ids, versions)
    assert [get_work_item(iid) for iid in (first, second)] == before


def test_previous_means_preceding_creation_even_if_older_task_was_edited(app_env):
    from checkpoint.services.review import get_work_item, update_work_item
    from checkpoint.services.task_evidence import bundle_previous
    older, _ = task(app_env, 'Older')
    previous, _ = task(app_env, 'Previous')
    current, _ = task(app_env, 'Current')
    update_work_item(older, {'description': 'Recently edited'})
    assert bundle_previous(current) == previous
    assert get_work_item(previous)['description'] == 'Previous\n\nCurrent'
    assert get_work_item(older)['description'] == 'Recently edited'


def test_bundle_into_newer_target_preserves_both_images(app_env):
    from checkpoint.services.review import get_work_item
    from checkpoint.services.task_evidence import bundle
    first, c1 = task(app_env, 'First')
    second, c2 = task(app_env, 'Second')
    assert bundle(first, second) == second
    item = get_work_item(second)
    assert item['description'] == 'First\n\nSecond'
    assert [e['capture_id'] for e in item['evidence'] if e['kind'] == 'capture'] == [c1, c2]


def test_order_migration_keeps_existing_tasks_and_attachments(app_env):
    from alembic import command
    from alembic.config import Config
    from checkpoint import db
    from checkpoint.config import paths
    from checkpoint.services.review import get_work_item
    from checkpoint.services.task_evidence import merge_work_items
    first, c1 = task(app_env, 'Existing first note')
    second, c2 = task(app_env, 'Existing second note')
    before = get_work_item(first)
    cfg = Config()
    cfg.set_main_option('script_location', str(db.MIGRATIONS_DIR))
    cfg.set_main_option('sqlalchemy.url', f'sqlite:///{paths().db_file.as_posix()}')
    with db.engine().begin() as conn:
        cfg.attributes['connection'] = conn
        command.downgrade(cfg, '0007')
    restart()
    assert get_work_item(first) == before
    merge_work_items([second, first])
    assert [e['capture_id'] for e in get_work_item(first)['evidence'] if e['kind'] == 'capture'] == [c1, c2]
