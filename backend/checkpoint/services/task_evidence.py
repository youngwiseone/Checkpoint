"""Explicit bundling and image removal, shared by the native editors."""
from sqlalchemy import select

from ..db import read_session, write_session
from ..events import hooks
from ..models import Capture, ChecklistEntry, DraftItem, EvidenceLink, WorkItem, utcnow
from ..storage import media_path


def last_task(project_id, exclude=None, before=None, *, open_only=True):
    with read_session() as s:
        query = select(WorkItem).where(WorkItem.project_id == project_id,
            WorkItem.work_status.in_(('open', 'in_progress') if open_only else ('open', 'in_progress', 'done')),
            WorkItem.duplicate_of_id.is_(None))
        if exclude:
            query = query.where(WorkItem.id != exclude)
        if before:
            query = query.where(WorkItem.created_at < before)
        order = ((WorkItem.created_at.desc(), WorkItem.id.desc()) if before else
                 (WorkItem.human_updated_at.desc(), WorkItem.created_at.desc(), WorkItem.id.desc()))
        item = s.scalars(query.order_by(*order)).first()
        return item.id if item else None


def capture_editor_payload(capture_id):
    from .inbox import ensure_capture
    from .review import get_work_item
    iid = ensure_capture(capture_id)
    item = get_work_item(iid)
    with read_session() as s:
        c = s.get(Capture, capture_id)
        return {'id': c.id, 'item_id': iid, 'project_id': c.project_id,
                'thumb_path': str(media_path(c.thumb_rel_path)), 'text': item['description'],
                'category': item['type'], 'evidence': item['evidence']}


def last_editor_payload(project_id):
    iid = last_task(project_id, open_only=False)
    if not iid:
        return None
    from .review import get_work_item
    item = get_work_item(iid)
    captures = [e for e in item['evidence'] if e['kind'] == 'capture' and not e.get('media_removed')]
    if captures:
        return capture_editor_payload(captures[-1]['capture_id'])
    return {'item_id': iid, 'project_id': project_id, 'text': item['description'],
            'category': item['type'], 'evidence': item['evidence']}


def accept_draft_capture(item_id, draft):
    """Persist a temporary edit screenshot only after its tab is explicitly chosen."""
    from .capture import CaptureService
    from .review import get_work_item
    item = get_work_item(item_id)
    if item['work_status'] not in ('open', 'in_progress', 'done'):
        raise ValueError('Reopen the task before adding this screenshot.')
    if draft.get('project_id') != item['project_id']:
        raise ValueError('The draft screenshot belongs to another project.')
    return CaptureService(None).import_image(
        draft['png'], draft['thumb_jpeg'], draft['width'], draft['height'],
        project_id=item['project_id'], session_id=draft.get('session_id'),
        offset_ms=draft.get('offset_ms'), taken_at=draft['taken_at'], status='saved',
        trigger='edit', attach_to=item_id, monitor=draft.get('monitor', {}),
        window_title=draft.get('window_title', ''))


def attach_capture(item_id, capture_id):
    with write_session() as s:
        w, c = s.get(WorkItem, item_id), s.get(Capture, capture_id)
        if not w or not c or w.project_id != c.project_id or c.status == 'discarded' or c.media_removed_at:
            raise ValueError('Choose a screenshot from the same project.')
        existing = s.scalars(select(EvidenceLink).where(EvidenceLink.capture_id == c.id,
            EvidenceLink.work_item_id.is_not(None))).all()
        if any(l.work_item_id != w.id for l in existing):
            raise ValueError('This screenshot belongs to another task. Bundle the tasks first.')
        if not existing:
            s.add(EvidenceLink(work_item_id=w.id, capture_id=c.id, confidence='direct', attached_by='user'))
        c.status = 'saved'
        # New evidence can be added to a text-only task after it is reopened.
        w.media_removed_at = None
        w.version += 1
        w.human_updated_at = utcnow()
    hooks.emit_state()
    hooks.emit_task_ready(item_id)


def detach_capture(item_id, capture_id):
    with write_session() as s:
        w = s.get(WorkItem, item_id)
        if not w:
            raise ValueError('Task not found')
        for link in s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == w.id,
                                                      EvidenceLink.capture_id == capture_id)).all():
            s.delete(link)
        # Old drafts must not reattach an image on a later organise pass.
        drafts = s.scalars(select(DraftItem.id).where(DraftItem.work_item_id == w.id)).all()
        if drafts:
            for link in s.scalars(select(EvidenceLink).where(EvidenceLink.draft_id.in_(drafts),
                                                           EvidenceLink.capture_id == capture_id)).all():
                s.delete(link)
        c = s.get(Capture, capture_id)
        s.flush()
        other = s.scalars(select(EvidenceLink.id).where(EvidenceLink.capture_id == capture_id,
                                                       EvidenceLink.work_item_id.is_not(None))).first()
        if c and not other:
            c.status = 'discarded'
        w.version += 1
        w.human_updated_at = utcnow()
    hooks.emit_state()
    hooks.emit_task_ready(item_id)


def previous_task(item_id):
    with read_session() as s:
        w = s.get(WorkItem, item_id)
        if not w:
            raise ValueError('Task not found')
        project_id, before = w.project_id, w.created_at
    target_id = last_task(project_id, exclude=item_id, before=before)
    if not target_id:
        raise ValueError('There is no earlier open task in this project.')
    return target_id


def bundle_previous(item_id):
    return bundle(item_id, previous_task(item_id))


def bundle(item_id, target_id):
    if item_id == target_id:
        raise ValueError('Choose another task to bundle with.')
    return merge_work_items([target_id, item_id], target_id=target_id)


def merge_work_items(item_ids, expected_versions=None, *, target_id=None):
    """Merge exactly these tasks atomically, appending oldest to newest."""
    from .review import evidence_payload
    ids = set(item_ids)
    if len(ids) < 2:
        raise ValueError('Select at least two tasks to merge.')
    with write_session() as s:
        items = list(s.scalars(select(WorkItem).where(WorkItem.id.in_(ids))
                              .order_by(WorkItem.created_at, WorkItem.id)).all())
        if len(items) != len(ids):
            raise ValueError('Task not found. Refresh your selection.')
        if len({item.project_id for item in items}) != 1:
            raise ValueError('Tasks must belong to the same project.')
        for item in items:
            if item.duplicate_of_id:
                raise ValueError('A selected task has already been merged. Refresh your selection.')
            if expected_versions is not None and expected_versions.get(item.id) != item.version:
                raise ValueError('A selected task changed elsewhere. Refresh your selection.')
        target = next((item for item in items if item.id == target_id), None) if target_id else items[0]
        if target is None:
            raise ValueError('The destination must be one of the selected tasks.')
        target_id = target.id
        groups = []
        for item in items:
            links = list(s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == item.id)
                                   .order_by(EvidenceLink.created_at, EvidenceLink.id)).all())
            by_id = {link.id: link for link in links}
            groups.append((item, [by_id[entry['id']] for entry in
                                  evidence_payload(s, links, work_item_order=True)]))
        keys, text = set(), []
        position = 0
        for item, links in groups:
            extra = item.description.strip() or (item.title if not item.title.startswith('Screenshot · ') else '')
            if extra:
                text.append(extra)
            for link in links:
                key = (link.capture_id, link.note_id, link.segment_id)
                if key in keys:
                    s.delete(link)
                    continue
                keys.add(key)
                link.work_item_id, link.work_item_order = target_id, position
                position += 1
            if item.id == target_id:
                continue
            for model in (DraftItem, ChecklistEntry):
                for row in s.scalars(select(model).where(model.work_item_id == item.id)).all():
                    row.work_item_id = target_id
            item.work_status, item.feedback_reason, item.duplicate_of_id = 'deleted', 'duplicate', target_id
            item.status_changed_at = utcnow()
            item.version += 1
        target.description = '\n\n'.join(text)
        target.description_edited = True
        if any(item.media_removed_at is None for item in items):
            target.media_removed_at = None
        target.version += 1
        target.human_updated_at = utcnow()
    hooks.emit_state()
    hooks.emit_task_ready(target_id)
    return target_id


def feedback(item_id, reason):
    if reason not in ('expected', 'misheard'):
        raise ValueError('Unknown feedback reason')
    from .review import update_work_item
    update_work_item(item_id, {'work_status': 'wont_do'})
    with write_session() as s:
        s.get(WorkItem, item_id).feedback_reason = reason
    hooks.emit_state()
