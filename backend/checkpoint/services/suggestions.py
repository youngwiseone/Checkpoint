"""Turn background captures into review suggestions while the session is still running.

An F8 press with audio recording, or an auto screenshot, only saves a marker. Once the speech
around it has been transcribed, the marker becomes a pending card (origin "suggestion") built
from what was said, so it shows up in the review overlay without ending the session or waiting
for the organiser. A marker with no speech nearby becomes a card that asks for a note.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from ..db import read_session, write_session
from ..models import Capture, DraftItem, EvidenceLink, Session, TranscriptSegment
from ..settings_store import get_settings
from ..timebase import fmt_offset
from .capture import capture_context_state, guess_type, provisional_title

log = logging.getLogger(__name__)

# What was said just before and after the press: people describe what they've just seen.
BEFORE_S, AFTER_S = 20, 15


def _pending_markers(s, session_id: str) -> list[Capture]:  # noqa: ANN001
    used = set(s.scalars(select(EvidenceLink.capture_id).where(EvidenceLink.capture_id.is_not(None),
                                                                EvidenceLink.draft_id.is_not(None))).all())
    caps = s.scalars(select(Capture).where(Capture.session_id == session_id, Capture.status == "marker",
                                           Capture.offset_ms.is_not(None)).order_by(Capture.offset_ms)).all()
    return [c for c in caps if c.id not in used]


def _card_text(c: Capture, segs: list[TranscriptSegment]) -> tuple[str, str]:
    said = " ".join((g.corrected_text or g.text).strip() for g in segs).strip()
    focus = (c.reason or "").strip() or said
    title = provisional_title(focus) if focus else f"Screenshot at {fmt_offset(c.offset_ms)}"
    return title, said


def promote(session_id: str) -> int:
    """Create suggestion cards for markers whose context is ready. Returns how many were created."""
    st = get_settings().ai
    made = 0
    with read_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            return 0
        ready: list[tuple[str, str]] = []
        for c in _pending_markers(s, session_id):
            state = capture_context_state(sess, c, max(BEFORE_S, st.window_before_s), AFTER_S, s)
            if state == "awaiting_transcription":
                continue
            if state == "speech_nearby" and sess.state in ("active", "paused"):
                # Wait until the speech after the press is transcribed too (or the session ends).
                last = s.scalars(select(TranscriptSegment.end_ms).where(TranscriptSegment.session_id == session_id)
                                 .order_by(TranscriptSegment.end_ms.desc())).first() or 0
                if last < c.offset_ms + AFTER_S * 1000:
                    continue
            ready.append((c.id, state))
    for cid, state in ready:
        if _make_card(session_id, cid, state):
            made += 1
    return made


def _make_card(session_id: str, capture_id: str, state: str) -> Optional[str]:
    with write_session() as s:
        c = s.get(Capture, capture_id)
        if c is None or c.status != "marker":
            return None
        if s.scalars(select(EvidenceLink.id).where(EvidenceLink.capture_id == c.id, EvidenceLink.draft_id.is_not(None))).first():
            return None  # linked by someone else meanwhile
        lo, hi = c.offset_ms - BEFORE_S * 1000, c.offset_ms + AFTER_S * 1000
        segs = s.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == session_id, TranscriptSegment.end_ms >= lo,
                                                         TranscriptSegment.start_ms <= hi).order_by(TranscriptSegment.start_ms)).all()
        title, said = _card_text(c, segs)
        needs_note = not said
        d = DraftItem(project_id=c.project_id, session_id=session_id, origin="suggestion",
                      type=guess_type(c.reason or said) if said else "note", title=title[:300], description=said,
                      needs_context=needs_note,
                      uncertainty=None if said else "Nothing was said near this screenshot. Add a note or dismiss it.")
        s.add(d)
        s.flush()
        s.add(EvidenceLink(draft_id=d.id, capture_id=c.id, confidence="direct", role="source", attached_by="system"))
        for g in segs:
            s.add(EvidenceLink(draft_id=d.id, segment_id=g.id, confidence="candidate", role="source", attached_by="system"))
        return d.id


def clear_untouched(session_id: str) -> int:
    """Before the organiser rebuilds a session's cards, drop suggestions nobody has reviewed yet
    (the organiser makes better cards from the same speech and links the screenshots itself)."""
    with write_session() as s:
        rows = s.scalars(select(DraftItem).where(DraftItem.session_id == session_id, DraftItem.origin == "suggestion",
                                                 DraftItem.review_state == "pending", DraftItem.human_edited.is_(False))).all()
        for d in rows:
            s.delete(d)
        return len(rows)
