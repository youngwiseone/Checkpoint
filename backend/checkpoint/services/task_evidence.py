"""Explicit bundling and image removal, shared by the native editors."""
from sqlalchemy import select

from ..db import read_session, write_session
from ..events import hooks
from ..models import Capture, DraftItem, EvidenceLink, WorkItem, utcnow
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
        item = s.scalars(query.order_by(WorkItem.human_updated_at.desc(), WorkItem.created_at.desc())).first()
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
    captures = [e for e in item['evidence'] if e['kind'] == 'capture']
    if captures:
        return capture_editor_payload(captures[-1]['capture_id'])
    return {'item_id': iid, 'project_id': project_id, 'text': item['description'],
            'category': item['type'], 'evidence': item['evidence']}


def attach_capture(item_id, capture_id):
    with write_session() as s:
        w, c = s.get(WorkItem, item_id), s.get(Capture, capture_id)
        if not w or not c or w.project_id != c.project_id or c.status == 'discarded':
            raise ValueError('Choose a screenshot from the same project.')
        existing = s.scalars(select(EvidenceLink).where(EvidenceLink.capture_id == c.id,
            EvidenceLink.work_item_id.is_not(None))).all()
        if any(l.work_item_id != w.id for l in existing):
            raise ValueError('This screenshot belongs to another task. Bundle the tasks first.')
        if not existing:
            s.add(EvidenceLink(work_item_id=w.id, capture_id=c.id, confidence='direct', attached_by='user'))
        c.status = 'saved'
        w.version += 1
        w.human_updated_at = utcnow()
    hooks.emit_state()


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


def bundle_previous(item_id):
    with read_session() as s:
        w = s.get(WorkItem, item_id)
        if not w:
            raise ValueError('Task not found')
        project_id, before = w.project_id, w.created_at
    target_id = last_task(project_id, exclude=item_id, before=before)
    if not target_id:
        raise ValueError('There is no earlier open task in this project.')
    return bundle(item_id, target_id)


def bundle(item_id, target_id):
    if item_id == target_id:
        raise ValueError('Choose another task to bundle with.')
    with write_session() as s:
        source, target = s.get(WorkItem, item_id), s.get(WorkItem, target_id)
        if not source or not target or source.project_id != target.project_id:
            raise ValueError('Tasks must belong to the same project.')
        links = s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == target.id)).all()
        keys = {(l.capture_id, l.note_id, l.segment_id) for l in links}
        for l in s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == source.id)).all():
            key = (l.capture_id, l.note_id, l.segment_id)
            if key not in keys:
                l.work_item_id = target.id
                keys.add(key)
            else:
                s.delete(l)
        extra = source.description.strip() or (source.title if not source.title.startswith('Screenshot · ') else '')
        if extra and extra not in target.description:
            target.description = '\n\n'.join(p for p in (target.description.strip(), extra) if p)
            target.description_edited = True
        for d in s.scalars(select(DraftItem).where(DraftItem.work_item_id == source.id)).all():
            d.work_item_id = target.id
        source.work_status, source.feedback_reason, source.duplicate_of_id = 'deleted', 'duplicate', target.id
        source.version += 1
        target.version += 1
        target.human_updated_at = utcnow()
    hooks.emit_state()
    return target_id


def feedback(item_id, reason):
    if reason not in ('expected', 'misheard'):
        raise ValueError('Unknown feedback reason')
    from .review import update_work_item
    update_work_item(item_id, {'work_status': 'wont_do'})
    with write_session() as s:
        s.get(WorkItem, item_id).feedback_reason = reason
    hooks.emit_state()
