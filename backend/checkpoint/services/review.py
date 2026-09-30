"""Review cards (drafts), canonical work items, evidence, checklist and recap."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, select

from ..db import read_session, write_session
from ..events import hooks
from ..models import (
    ITEM_TYPES,
    WORK_STATUSES,
    AudioSource,
    Capture,
    ChecklistEntry,
    DraftItem,
    EvidenceLink,
    Note,
    Project,
    Session,
    SyncJob,
    TranscriptSegment,
    WorkItem,
    utcnow,
)
from .capture import provisional_title


class ReviewError(ValueError):
    pass


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


# ---------------------------------------------------------------- serialisation
def evidence_payload(s, links: Iterable[EvidenceLink]) -> list[dict]:  # noqa: ANN001
    out = []
    labels: dict[str, str] = {}
    for link in links:
        base = {"id": link.id, "confidence": link.confidence, "role": link.role, "attached_by": link.attached_by}
        if link.capture_id:
            c = s.get(Capture, link.capture_id)
            if c is None:
                continue
            out.append({**base, "kind": "capture", "capture_id": c.id, "offset_ms": c.offset_ms, "taken_at": _iso(c.taken_at),
                        "thumb_url": f"/api/media/captures/{c.id}/thumb", "image_url": f"/api/media/captures/{c.id}/image",
                        "window_title": c.window_title, "width": c.width, "height": c.height})
        elif link.segment_id:
            seg = s.get(TranscriptSegment, link.segment_id)
            if seg is None:
                continue
            if seg.source_id not in labels:
                src = s.get(AudioSource, seg.source_id)
                labels[seg.source_id] = (src.label if src and src.label else ("Microphone" if src and src.kind == "mic" else "Computer audio"))
            out.append({**base, "kind": "segment", "segment_id": seg.id, "offset_ms": seg.start_ms, "end_ms": seg.end_ms,
                        "text": seg.corrected_text or seg.text, "raw_text": seg.text, "corrected": seg.corrected_text is not None,
                        "source_label": labels[seg.source_id], "low_confidence": seg.low_confidence,
                        "audio_url": f"/api/segments/{seg.id}/audio" if seg.chunk_id else None})
        elif link.note_id:
            n = s.get(Note, link.note_id)
            if n is None:
                continue
            out.append({**base, "kind": "note", "note_id": n.id, "offset_ms": n.offset_ms, "text": n.text, "note_kind": n.kind,
                        "capture_id": n.capture_id})
    out.sort(key=lambda e: (e.get("offset_ms") is None, e.get("offset_ms") or 0))
    return out


def draft_dict(s, d: DraftItem, with_evidence: bool = True) -> dict:  # noqa: ANN001
    out = {
        "id": d.id, "project_id": d.project_id, "session_id": d.session_id, "origin": d.origin, "type": d.type,
        "title": d.title, "description": d.description, "review_state": d.review_state, "needs_context": d.needs_context,
        "uncertainty": d.uncertainty, "statement_kind": d.statement_kind, "withdrawn": d.withdrawn,
        "possibly_completed": d.possibly_completed, "conflict_note": d.conflict_note, "human_edited": d.human_edited,
        "work_item_id": d.work_item_id, "merged_into_id": d.merged_into_id, "version": d.version,
        "created_at": _iso(d.created_at), "updated_at": _iso(d.updated_at), "dismissed_at": _iso(d.dismissed_at),
    }
    if with_evidence:
        links = s.scalars(select(EvidenceLink).where(EvidenceLink.draft_id == d.id)).all()
        ev = evidence_payload(s, links)
        out["evidence"] = ev
        times = [e["offset_ms"] for e in ev if e.get("offset_ms") is not None]
        out["offset_ms"] = min(times) if times else None
        if d.session_id:
            sess = s.get(Session, d.session_id)
            out["session_title"] = sess.title if sess else None
    return out


def work_item_dict(s, w: WorkItem, with_evidence: bool = False) -> dict:  # noqa: ANN001
    out = {
        "id": w.id, "project_id": w.project_id, "session_id": w.session_id, "type": w.type, "title": w.title,
        "description": w.description, "work_status": w.work_status, "assignee": w.assignee, "priority": w.priority,
        "tags": w.tags or [], "version": w.version, "origin": w.origin, "created_by": w.created_by, "updated_by": w.updated_by,
        "sharing_state": w.sharing_state, "server_version": w.server_version, "sync_error": w.sync_error,
        "conflict_server_copy": w.conflict_server_copy, "shared_attachment_ids": w.shared_attachment_ids or [],
        "shared_excerpts": w.shared_excerpts, "last_synced_at": _iso(w.last_synced_at),
        "completed_at": _iso(w.completed_at), "created_at": _iso(w.created_at), "updated_at": _iso(w.updated_at),
    }
    if w.session_id:
        sess = s.get(Session, w.session_id)
        out["session_title"] = sess.title if sess else None
    links = s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == w.id)).all()
    out["capture_count"] = sum(1 for l in links if l.capture_id)
    if with_evidence:
        out["evidence"] = evidence_payload(s, links)
        if w.origin == "shared" and w.shared_attachment_ids:
            out["remote_attachments"] = [
                {"id": a, "url": f"/api/share/attachments/{a}"} for a in (w.shared_attachment_ids or [])
            ]
    else:
        first = next((l for l in links if l.capture_id), None)
        out["thumb_url"] = f"/api/media/captures/{first.capture_id}/thumb" if first else None
    return out


# ---------------------------------------------------------------- drafts
def list_drafts(project_id: Optional[str] = None, session_id: Optional[str] = None, state: Optional[str] = None) -> list[dict]:
    with read_session() as s:
        q = select(DraftItem).where(DraftItem.merged_into_id.is_(None))
        if project_id:
            q = q.where(DraftItem.project_id == project_id)
        if session_id:
            q = q.where(DraftItem.session_id == session_id)
        if state:
            q = q.where(DraftItem.review_state == state)
        q = q.order_by(DraftItem.created_at)
        items = [draft_dict(s, d) for d in s.scalars(q).all()]
    items.sort(key=lambda d: (d.get("session_id") or "", d.get("offset_ms") if d.get("offset_ms") is not None else 1 << 40, d["created_at"]))
    return items


def get_draft(draft_id: str) -> dict:
    with read_session() as s:
        d = s.get(DraftItem, draft_id)
        if d is None:
            raise ReviewError("Card not found")
        return draft_dict(s, d)


EDITABLE_DRAFT = {"type", "title", "description", "needs_context"}


def update_draft(draft_id: str, patch: dict[str, Any], expected_version: Optional[int] = None) -> dict:
    with write_session() as s:
        d = s.get(DraftItem, draft_id)
        if d is None:
            raise ReviewError("Card not found")
        if expected_version is not None and expected_version != d.version:
            raise ReviewError("This card changed elsewhere. Reload to see the latest version.")
        for k, v in patch.items():
            if k not in EDITABLE_DRAFT:
                continue
            if k == "type" and v not in ITEM_TYPES:
                raise ReviewError("Unknown type")
            if k == "title":
                v = (v or "").strip()[:300]
                if not v:
                    raise ReviewError("Title can't be empty")
            setattr(d, k, v)
        d.human_edited = True
        d.version += 1
        # Keep an approved card's canonical item in step with edits made in review.
        if d.review_state == "approved" and d.work_item_id:
            w = s.get(WorkItem, d.work_item_id)
            if w:
                w.title, w.description, w.type = d.title, d.description, d.type
                _bump_work_item(s, w)
        s.flush()
        return draft_dict(s, d)


def _bump_work_item(s, w: WorkItem, who: Optional[str] = None) -> None:  # noqa: ANN001
    w.version += 1
    w.updated_at = utcnow()
    if who:
        w.updated_by = who
    if w.sharing_state in ("synced", "failed", "pending", "conflict") and w.server_version is not None:
        if w.sharing_state != "conflict":
            w.sharing_state = "pending"
            _enqueue_sync(s, w)


def _enqueue_sync(s, w: WorkItem) -> None:  # noqa: ANN001
    existing = s.scalars(select(SyncJob).where(SyncJob.work_item_id == w.id, SyncJob.state.in_(("pending", "failed")))).first()
    if existing:
        existing.state = "pending"
        existing.next_attempt_at = utcnow()
        existing.attempts = 0
    else:
        s.add(SyncJob(work_item_id=w.id, op="upsert"))


def approve(draft_ids: list[str]) -> list[dict]:
    """Transactional + idempotent: each draft maps to exactly one canonical work item."""
    from ..settings_store import get_settings

    who = get_settings().sharing.display_name or None
    results = []
    with write_session() as s:
        for did in dict.fromkeys(draft_ids):
            d = s.get(DraftItem, did)
            if d is None:
                raise ReviewError("Card not found")
            if d.review_state == "dismissed":
                raise ReviewError(f"“{d.title}” is dismissed. Restore it before approving.")
            w = s.get(WorkItem, d.work_item_id) if d.work_item_id else None
            if w is None:
                w = WorkItem(project_id=d.project_id, session_id=d.session_id, type=d.type, title=d.title,
                             description=d.description, work_status="open", created_by=who, updated_by=who)
                s.add(w)
                s.flush()
                d.work_item_id = w.id
            elif d.review_state != "approved":
                w.title, w.description, w.type = d.title, d.description, d.type
                _bump_work_item(s, w, who)
            existing = {(l.capture_id, l.segment_id, l.note_id) for l in s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == w.id)).all()}
            for l in s.scalars(select(EvidenceLink).where(EvidenceLink.draft_id == d.id)).all():
                key = (l.capture_id, l.segment_id, l.note_id)
                if key not in existing:
                    s.add(EvidenceLink(work_item_id=w.id, capture_id=l.capture_id, segment_id=l.segment_id, note_id=l.note_id,
                                       confidence=l.confidence, role=l.role, attached_by=l.attached_by))
                    existing.add(key)
            d.review_state = "approved"
            d.version += 1
            results.append({"draft_id": d.id, "work_item_id": w.id})
    hooks.emit_state()
    return results


def dismiss(draft_id: str) -> dict:
    with write_session() as s:
        d = s.get(DraftItem, draft_id)
        if d is None:
            raise ReviewError("Card not found")
        if d.review_state == "approved":
            raise ReviewError("Approved cards can't be dismissed — set the item's work status to “Won't do” instead.")
        d.review_state = "dismissed"
        d.dismissed_at = utcnow()
        d.version += 1
        return draft_dict(s, d, with_evidence=False)


def restore(draft_id: str) -> dict:
    """Undo a dismissal (also restores a withdrawn suggestion deliberately)."""
    with write_session() as s:
        d = s.get(DraftItem, draft_id)
        if d is None:
            raise ReviewError("Card not found")
        if d.review_state == "dismissed":
            d.review_state = "pending"
            d.dismissed_at = None
            d.human_edited = True
            d.version += 1
        return draft_dict(s, d, with_evidence=False)


def create_draft(project_id: str, session_id: Optional[str], type_: str, title: str, description: str,
                 segment_ids: list[str] | None = None, note_ids: list[str] | None = None,
                 capture_ids: list[str] | None = None, origin: str = "manual") -> dict:
    if type_ not in ITEM_TYPES:
        raise ReviewError("Unknown type")
    title = (title or "").strip() or provisional_title(description or "")
    if not title or title == "Untitled":
        raise ReviewError("Give the card a title or description")
    with write_session() as s:
        if s.get(Project, project_id) is None:
            raise ReviewError("Project not found")
        d = DraftItem(project_id=project_id, session_id=session_id, origin=origin, type=type_, title=title[:300],
                      description=description or "", human_edited=True)
        s.add(d)
        s.flush()
        _attach(s, d.id, segment_ids or [], note_ids or [], capture_ids or [], session_id)
        s.flush()
        return draft_dict(s, d)


def _attach(s, draft_id: str, segment_ids, note_ids, capture_ids, session_id) -> None:  # noqa: ANN001
    for sid in dict.fromkeys(segment_ids):
        seg = s.get(TranscriptSegment, sid)
        if seg is None or (session_id and seg.session_id != session_id):
            raise ReviewError("Unknown transcript segment")
        s.add(EvidenceLink(draft_id=draft_id, segment_id=sid, confidence="direct", attached_by="user"))
    for nid in dict.fromkeys(note_ids):
        if s.get(Note, nid) is None:
            raise ReviewError("Unknown note")
        s.add(EvidenceLink(draft_id=draft_id, note_id=nid, confidence="direct", attached_by="user"))
    for cid in dict.fromkeys(capture_ids):
        if s.get(Capture, cid) is None:
            raise ReviewError("Unknown screenshot")
        s.add(EvidenceLink(draft_id=draft_id, capture_id=cid, confidence="direct", attached_by="user"))


def attach_capture(draft_id: str, capture_id: str) -> dict:
    with write_session() as s:
        d = s.get(DraftItem, draft_id)
        c = s.get(Capture, capture_id)
        if d is None or c is None:
            raise ReviewError("Not found")
        link = s.scalars(select(EvidenceLink).where(EvidenceLink.draft_id == draft_id, EvidenceLink.capture_id == capture_id)).first()
        if link is None:
            s.add(EvidenceLink(draft_id=draft_id, capture_id=capture_id, confidence="direct", attached_by="user"))
        else:
            link.confidence = "direct"
            link.attached_by = "user"
        if c.status in ("unfinished", "marker"):
            c.status = "saved"
        d.human_edited = True
        d.version += 1
        s.flush()
        return draft_dict(s, d)


def detach_evidence(draft_id: str, link_id: str) -> dict:
    with write_session() as s:
        link = s.get(EvidenceLink, link_id)
        d = s.get(DraftItem, draft_id)
        if link is None or d is None or link.draft_id != draft_id:
            raise ReviewError("Not found")
        s.delete(link)
        d.human_edited = True
        d.version += 1
        s.flush()
        return draft_dict(s, d)


def merge(draft_ids: list[str], title: Optional[str] = None, description: Optional[str] = None) -> dict:
    ids = list(dict.fromkeys(draft_ids))
    if len(ids) < 2:
        raise ReviewError("Select at least two cards to merge")
    with write_session() as s:
        drafts = [s.get(DraftItem, i) for i in ids]
        if any(d is None for d in drafts):
            raise ReviewError("Card not found")
        if any(d.review_state != "pending" for d in drafts):
            raise ReviewError("Only pending cards can be merged")
        target = drafts[0]
        seen = {(l.capture_id, l.segment_id, l.note_id) for l in s.scalars(select(EvidenceLink).where(EvidenceLink.draft_id == target.id)).all()}
        for d in drafts[1:]:
            for l in s.scalars(select(EvidenceLink).where(EvidenceLink.draft_id == d.id)).all():
                key = (l.capture_id, l.segment_id, l.note_id)
                if key not in seen:
                    s.add(EvidenceLink(draft_id=target.id, capture_id=l.capture_id, segment_id=l.segment_id, note_id=l.note_id,
                                       confidence=l.confidence, role=l.role, attached_by=l.attached_by))
                    seen.add(key)
            d.merged_into_id = target.id
            d.review_state = "dismissed"
            d.dismissed_at = utcnow()
        target.title = (title or target.title).strip()[:300]
        if description is not None:
            target.description = description
        else:
            target.description = "\n\n".join(x for x in [target.description] + [d.description for d in drafts[1:]] if x)
        target.human_edited = True
        target.needs_context = all(d.needs_context for d in drafts)
        target.version += 1
        s.flush()
        return draft_dict(s, target)


def split(draft_id: str, parts: list[dict]) -> list[dict]:
    """Split an over-broad card. Each part: {title, description, type?, evidence_link_ids?}."""
    if len(parts) < 2:
        raise ReviewError("Provide at least two parts")
    with write_session() as s:
        d = s.get(DraftItem, draft_id)
        if d is None:
            raise ReviewError("Card not found")
        if d.review_state != "pending":
            raise ReviewError("Only pending cards can be split")
        links = {l.id: l for l in s.scalars(select(EvidenceLink).where(EvidenceLink.draft_id == d.id)).all()}
        created = []
        for p in parts:
            title = (p.get("title") or "").strip()
            if not title:
                raise ReviewError("Each part needs a title")
            t = p.get("type") or d.type
            if t not in ITEM_TYPES:
                raise ReviewError("Unknown type")
            nd = DraftItem(project_id=d.project_id, session_id=d.session_id, origin="split", type=t, title=title[:300],
                           description=p.get("description") or "", human_edited=True, statement_kind=d.statement_kind,
                           needs_context=d.needs_context)
            s.add(nd)
            s.flush()
            chosen = p.get("evidence_link_ids")
            for lid, l in links.items():
                if chosen is None or lid in chosen:
                    s.add(EvidenceLink(draft_id=nd.id, capture_id=l.capture_id, segment_id=l.segment_id, note_id=l.note_id,
                                       confidence=l.confidence, role=l.role, attached_by=l.attached_by))
            created.append(nd)
        d.review_state = "dismissed"
        d.dismissed_at = utcnow()
        d.conflict_note = "Split into separate cards"
        d.version += 1
        s.flush()
        return [draft_dict(s, x) for x in created]


def review_counts(project_id: Optional[str] = None) -> dict:
    with read_session() as s:
        q = select(DraftItem.review_state, func.count()).where(DraftItem.merged_into_id.is_(None)).group_by(DraftItem.review_state)
        if project_id:
            q = q.where(DraftItem.project_id == project_id)
        counts = {k: v for k, v in s.execute(q).all()}
        uq = select(func.count()).select_from(Capture).where(Capture.status == "unfinished")
        if project_id:
            uq = uq.where(Capture.project_id == project_id)
        unfinished = s.scalar(uq) or 0
    return {"pending": counts.get("pending", 0), "approved": counts.get("approved", 0), "dismissed": counts.get("dismissed", 0),
            "unfinished_captures": unfinished}


# ---------------------------------------------------------------- work items
def list_work_items(project_id: Optional[str] = None, q: str = "", types: list[str] | None = None,
                    statuses: list[str] | None = None, session_id: Optional[str] = None) -> list[dict]:
    with read_session() as s:
        stmt = select(WorkItem)
        if project_id:
            stmt = stmt.where(WorkItem.project_id == project_id)
        if types:
            stmt = stmt.where(WorkItem.type.in_(types))
        if statuses:
            stmt = stmt.where(WorkItem.work_status.in_(statuses))
        if session_id:
            stmt = stmt.where(WorkItem.session_id == session_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(WorkItem.title.ilike(like), WorkItem.description.ilike(like)))
        stmt = stmt.order_by(WorkItem.updated_at.desc())
        return [work_item_dict(s, w) for w in s.scalars(stmt).all()]


def get_work_item(item_id: str) -> dict:
    with read_session() as s:
        w = s.get(WorkItem, item_id)
        if w is None:
            raise ReviewError("Item not found")
        return work_item_dict(s, w, with_evidence=True)


EDITABLE_WORK = {"type", "title", "description", "work_status", "assignee", "priority", "tags"}


def update_work_item(item_id: str, patch: dict[str, Any], expected_version: Optional[int] = None) -> dict:
    from ..settings_store import get_settings

    who = get_settings().sharing.display_name or None
    with write_session() as s:
        w = s.get(WorkItem, item_id)
        if w is None:
            raise ReviewError("Item not found")
        if expected_version is not None and expected_version != w.version:
            raise ReviewError("This item changed elsewhere. Reload to see the latest version.")
        changed = False
        for k, v in patch.items():
            if k not in EDITABLE_WORK:
                continue
            if k == "type" and v not in ITEM_TYPES:
                raise ReviewError("Unknown type")
            if k == "work_status":
                if v not in WORK_STATUSES:
                    raise ReviewError("Unknown status")
                w.completed_at = utcnow() if v == "done" else None
            if k == "title":
                v = (v or "").strip()[:300]
                if not v:
                    raise ReviewError("Title can't be empty")
            if k == "tags":
                v = [str(t)[:40] for t in (v or [])][:20]
            if getattr(w, k) != v:
                setattr(w, k, v)
                changed = True
        if changed:
            _bump_work_item(s, w, who)
            # Keep a linked checklist entry in step with explicit status changes.
            for e in s.scalars(select(ChecklistEntry).where(ChecklistEntry.work_item_id == w.id)).all():
                e.state = "done" if w.work_status == "done" else ("open" if e.state == "done" else e.state)
                e.completed_at = w.completed_at
        s.flush()
        return work_item_dict(s, w, with_evidence=True)


def create_work_item(project_id: str, type_: str, title: str, description: str = "", session_id: Optional[str] = None) -> dict:
    if type_ not in ITEM_TYPES:
        raise ReviewError("Unknown type")
    if not title.strip():
        raise ReviewError("Title can't be empty")
    with write_session() as s:
        w = WorkItem(project_id=project_id, session_id=session_id, type=type_, title=title.strip()[:300], description=description)
        s.add(w)
        s.flush()
        return work_item_dict(s, w)


# ---------------------------------------------------------------- checklist & recap
def checklist_dict(e: ChecklistEntry) -> dict:
    return {"id": e.id, "session_id": e.session_id, "text": e.text, "work_item_id": e.work_item_id, "origin": e.origin,
            "state": e.state, "suggested_done": e.suggested_done, "suggestion_segment_ids": e.suggestion_segment_ids or [],
            "position": e.position, "completed_at": _iso(e.completed_at)}


def list_checklist(session_id: str) -> list[dict]:
    with read_session() as s:
        rows = s.scalars(select(ChecklistEntry).where(ChecklistEntry.session_id == session_id).order_by(ChecklistEntry.position, ChecklistEntry.created_at)).all()
        return [checklist_dict(e) for e in rows]


def add_checklist(session_id: str, text: str, during: bool = True) -> dict:
    text = text.strip()
    if not text:
        raise ReviewError("Checklist text can't be empty")
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise ReviewError("Session not found")
        pos = (s.scalar(select(func.max(ChecklistEntry.position)).where(ChecklistEntry.session_id == session_id)) or 0) + 1
        e = ChecklistEntry(session_id=session_id, project_id=sess.project_id, text=text[:1000], origin="during" if during else "planned", position=pos)
        s.add(e)
        s.flush()
        return checklist_dict(e)


def bring_in(session_id: str, item_ids: list[str]) -> list[dict]:
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise ReviewError("Session not found")
        have = set(s.scalars(select(ChecklistEntry.work_item_id).where(ChecklistEntry.session_id == session_id)).all())
        pos = (s.scalar(select(func.max(ChecklistEntry.position)).where(ChecklistEntry.session_id == session_id)) or 0) + 1
        for iid in item_ids:
            if iid in have:
                continue
            w = s.get(WorkItem, iid)
            if w is None or w.project_id != sess.project_id:
                continue
            s.add(ChecklistEntry(session_id=session_id, project_id=sess.project_id, text=w.title, work_item_id=w.id,
                                 origin="brought_in", position=pos))
            pos += 1
    return list_checklist(session_id)


def set_checklist_state(entry_id: str, state: str) -> dict:
    if state not in ("open", "done", "outstanding"):
        raise ReviewError("Unknown state")
    with write_session() as s:
        e = s.get(ChecklistEntry, entry_id)
        if e is None:
            raise ReviewError("Checklist entry not found")
        e.state = state
        e.completed_at = utcnow() if state == "done" else None
        if state == "done":
            e.suggested_done = False
        if e.work_item_id:
            w = s.get(WorkItem, e.work_item_id)
            if w:
                new_status = "done" if state == "done" else ("open" if w.work_status == "done" else w.work_status)
                if new_status != w.work_status:
                    w.work_status = new_status
                    w.completed_at = e.completed_at
                    _bump_work_item(s, w)
        s.flush()
        return checklist_dict(e)


def dismiss_suggestion(entry_id: str) -> dict:
    with write_session() as s:
        e = s.get(ChecklistEntry, entry_id)
        if e is None:
            raise ReviewError("Checklist entry not found")
        e.suggested_done = False
        s.flush()
        return checklist_dict(e)


def delete_checklist(entry_id: str) -> None:
    with write_session() as s:
        e = s.get(ChecklistEntry, entry_id)
        if e:
            s.delete(e)


def keep_outstanding(session_id: str) -> int:
    """Turn open planned checks into project Tasks so nothing is forgotten."""
    n = 0
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise ReviewError("Session not found")
        for e in s.scalars(select(ChecklistEntry).where(ChecklistEntry.session_id == session_id, ChecklistEntry.state != "done")).all():
            if e.work_item_id:
                e.state = "outstanding"
                continue
            w = WorkItem(project_id=sess.project_id, session_id=session_id, type="task", title=e.text[:300],
                         description=f"Planned check carried over from “{sess.title}”.")
            s.add(w)
            s.flush()
            e.work_item_id = w.id
            e.state = "outstanding"
            n += 1
    return n


def recap(session_id: str) -> dict:
    with read_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise ReviewError("Session not found")
        entries = [checklist_dict(e) for e in s.scalars(select(ChecklistEntry).where(ChecklistEntry.session_id == session_id).order_by(ChecklistEntry.position)).all()]
        drafts = s.scalars(select(DraftItem).where(DraftItem.session_id == session_id, DraftItem.merged_into_id.is_(None))).all()
        done_items = s.scalars(select(WorkItem).where(WorkItem.session_id == session_id, WorkItem.work_status == "done")).all()
        return {
            "planned": [e for e in entries if e["origin"] in ("planned", "brought_in")],
            "added_during": [e for e in entries if e["origin"] == "during"],
            "completed": [e for e in entries if e["state"] == "done"],
            "outstanding": [e for e in entries if e["state"] != "done"],
            "suggested_done": [e for e in entries if e["suggested_done"] and e["state"] != "done"],
            "completed_items": [work_item_dict(s, w) for w in done_items],
            "new_items": {
                "pending": sum(1 for d in drafts if d.review_state == "pending"),
                "approved": sum(1 for d in drafts if d.review_state == "approved"),
                "dismissed": sum(1 for d in drafts if d.review_state == "dismissed"),
                "possibly_completed": [draft_dict(s, d, with_evidence=False) for d in drafts if d.possibly_completed and d.review_state != "dismissed"],
                "by_type": {t: sum(1 for d in drafts if d.type == t and d.review_state != "dismissed") for t in ITEM_TYPES},
            },
        }
