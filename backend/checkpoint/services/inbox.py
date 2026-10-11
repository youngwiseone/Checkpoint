"""One inbox, with speech attached to manual captures as it arrives."""
from sqlalchemy import func, select

from ..db import read_session, write_session
from ..models import Capture, DraftItem, EvidenceLink, TranscriptSegment, WorkItem
from ..workers import Worker
from .capture import guess_type, provisional_title
from .review import approve


def reconcile() -> None:
    # Retain the old draft records as evidence/history, without a separate approval step.
    with read_session() as s:
        pending = list(s.scalars(select(DraftItem.id).where(
            DraftItem.review_state == "pending", DraftItem.merged_into_id.is_(None))).all())
        dismissed_captures = set(s.scalars(select(EvidenceLink.capture_id).join(DraftItem, DraftItem.id == EvidenceLink.draft_id)
                                            .where(DraftItem.review_state == "dismissed")).all())
        unfinished = list(s.scalars(select(Capture.id).where(Capture.status.in_(("unfinished", "marker", "saved")))).all())
    if pending:
        approve(pending)
    for cid in unfinished:
        if cid not in dismissed_captures:
            ensure_capture(cid)
    with write_session() as s:
        rows = s.execute(select(Capture, WorkItem).join(EvidenceLink, EvidenceLink.capture_id == Capture.id)
                         .join(WorkItem, WorkItem.id == EvidenceLink.work_item_id)
                         .where(Capture.status.in_(("marker", "saved")), Capture.session_id.is_not(None))).all()
        for c, w in rows:
            if c.offset_ms is None:
                continue
            segments = s.scalars(select(TranscriptSegment).where(
                TranscriptSegment.session_id == c.session_id,
                TranscriptSegment.end_ms >= c.offset_ms - 20000,
                TranscriptSegment.start_ms <= c.offset_ms + 15000).order_by(TranscriptSegment.start_ms)).all()
            existing = set(s.scalars(select(EvidenceLink.segment_id).where(EvidenceLink.work_item_id == w.id)).all())
            added = False
            for seg in segments:
                if seg.id not in existing:
                    s.add(EvidenceLink(work_item_id=w.id, segment_id=seg.id, confidence="candidate", attached_by="system"))
                    existing.add(seg.id)
                    added = True
            # Never overwrite a title/note the user has edited. Speech remains available as evidence.
            if added and w.title.startswith("Screenshot · ") and not w.description and not w.description_edited:
                said = " ".join((g.corrected_text or g.text).strip() for g in segments)
                w.title, w.description, w.type = provisional_title(said), said, guess_type(said)
                w.version += 1


def ensure_capture(capture_id: str) -> str:
    with write_session() as s:
        c = s.get(Capture, capture_id)
        if c is None:
            raise ValueError("Screenshot not found")
        link = s.scalars(select(EvidenceLink).where(EvidenceLink.capture_id == c.id,
                                                  EvidenceLink.work_item_id.is_not(None))).first()
        if link:
            return link.work_item_id
        w = WorkItem(project_id=c.project_id, session_id=c.session_id, type="note",
                     title=f"Screenshot · {c.window_title or c.taken_at.strftime('%H:%M:%S')}"[:300],
                     description="", work_status="open", human_updated_at=c.taken_at)
        s.add(w)
        s.flush()
        s.add(EvidenceLink(work_item_id=w.id, capture_id=c.id, confidence="direct", attached_by="user"))
        if c.status == "unfinished":
            c.status = "marker"
        return w.id


class InboxWorker(Worker):
    name = "inbox"
    idle_sleep = 3

    def __init__(self):
        super().__init__()
        self.last_signature = None

    def step(self) -> bool:
        with read_session() as s:
            signature = (
                s.scalar(select(func.count(Capture.id))),
                s.scalar(select(func.count(TranscriptSegment.id))),
                s.scalar(select(func.count(DraftItem.id)).where(DraftItem.review_state == "pending")),
            )
        if signature != self.last_signature:
            reconcile()
            self.last_signature = signature
        from .image_cards import refresh
        refresh()
        return False
