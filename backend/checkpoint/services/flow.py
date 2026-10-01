"""The everyday loop behind the review overlay: pick the session, review its cards one at a
time, and send what was approved.

Kept free of Qt so the overlay window (desktop/overlay.py) and the web UI share one behaviour.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import or_, select

from ..db import read_session, write_session
from ..models import Capture, DraftItem, EvidenceLink, Note, Project, Session, TranscriptSegment, WorkItem, utcnow
from ..settings_store import get_settings
from ..storage import media_path
from . import agents
from . import review as rv
from .appwatch import rule_matches

RECENT = timedelta(days=7)
LOOSE = timedelta(hours=24)


def _has_open_work(s, session_id: str) -> bool:  # noqa: ANN001
    if s.scalars(select(DraftItem.id).where(DraftItem.session_id == session_id, DraftItem.review_state == "pending",
                                            DraftItem.merged_into_id.is_(None))).first():
        return True
    return bool(s.scalars(select(WorkItem.id).where(
        WorkItem.session_id == session_id, WorkItem.origin == "local",
        or_(WorkItem.agent_state.in_(("sending", "sent", "working", "checking", "needs_you")),
            (WorkItem.agent_state.is_(None) & WorkItem.duplicate_of_id.is_(None) & WorkItem.work_status.in_(("open", "in_progress")))),
    )).first())


def _latest(s, project_id: Optional[str] = None) -> Optional[Session]:  # noqa: ANN001
    q = select(Session).where(Session.is_demo.is_(False)).order_by(Session.started_at.desc())
    if project_id:
        q = q.where(Session.project_id == project_id)
    return s.scalars(q).first()


def project_for_window(window: Optional[dict]) -> Optional[str]:
    """The project whose program is in the foreground, if a watch rule says so."""
    if not window:
        return None
    for rule in get_settings().app_watch.rules:
        if rule_matches(rule, [window]):
            return rule.project_id
    return None


def target(active_session_id: Optional[str], window: Optional[dict] = None) -> dict:
    """Which session the overlay opens on. Asks (returns choices) only when it's genuinely unclear."""
    if active_session_id:
        return {"session_id": active_session_id}
    with read_session() as s:
        pid = project_for_window(window)
        if pid:
            sess = _latest(s, pid)
            if sess:
                return {"session_id": sess.id}
        since = utcnow() - RECENT
        recent = s.scalars(select(Session).where(Session.is_demo.is_(False), Session.started_at >= since)
                           .order_by(Session.started_at.desc()).limit(40)).all()
        open_ = [x for x in recent if _has_open_work(s, x.id)]
        projects = list(dict.fromkeys(x.project_id for x in open_))
        if len(projects) == 1:
            return {"session_id": open_[0].id}
        if len(projects) > 1:
            choices = []
            for p in projects[:6]:
                sess = next(x for x in open_ if x.project_id == p)
                proj = s.get(Project, p)
                choices.append({"session_id": sess.id, "project": proj.name if proj else "?", "title": sess.title})
            return {"choices": choices}
        last_pid = get_settings().last_project_id
        sess = _latest(s, last_pid) or _latest(s)
        return {"session_id": sess.id} if sess else {"empty": True}


def adopt_loose(session_id: str) -> int:
    """Cards captured outside any session in the last day join this session's review."""
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            return 0
        rows = s.scalars(select(DraftItem).where(DraftItem.project_id == sess.project_id, DraftItem.session_id.is_(None),
                                                 DraftItem.review_state == "pending", DraftItem.merged_into_id.is_(None),
                                                 DraftItem.created_at >= utcnow() - LOOSE)).all()
        for d in rows:
            d.session_id = session_id
        return len(rows)


def _card(s, d: DraftItem, sent: list[WorkItem]) -> dict:  # noqa: ANN001
    links = s.scalars(select(EvidenceLink).where(EvidenceLink.draft_id == d.id)).all()
    image = None
    quotes: list[str] = []
    for l in sorted(links, key=lambda l: l.confidence != "direct"):
        if l.capture_id and image is None:
            c = s.get(Capture, l.capture_id)
            if c:
                image = {"path": str(media_path(c.image_rel_path)), "url": f"/api/media/captures/{c.id}/image",
                         "possibly_related": l.confidence != "direct", "reason": c.reason}
        elif l.segment_id and len(quotes) < 4:
            g = s.get(TranscriptSegment, l.segment_id)
            if g:
                quotes.append((g.corrected_text or g.text).strip())
        elif l.note_id and len(quotes) < 4:
            n = s.get(Note, l.note_id)
            if n and n.text.strip() != (d.description or "").strip():
                quotes.append(n.text.strip())
    like = next((w for w in sent if agents.similar(d.title, w.title)), None)
    return {"id": d.id, "title": d.title, "description": d.description, "type": d.type, "origin": d.origin,
            "needs_context": d.needs_context, "uncertainty": d.uncertainty, "version": d.version, "image": image,
            "quotes": quotes,
            "similar_to": ({"code": agents.task_code(like.task_number), "title": like.title, "state": like.agent_state}
                           if like else None)}


def state(session_id: str, previews=None) -> dict:  # noqa: ANN001
    """Everything the overlay shows: the pending cards in order, and where the session's tasks stand."""
    flow = agents.session_flow(session_id, previews)
    with read_session() as s:
        sent = s.scalars(select(WorkItem).where(WorkItem.session_id == session_id, WorkItem.agent_state.is_not(None))
                         .order_by(WorkItem.task_number)).all()
        drafts = s.scalars(select(DraftItem).where(DraftItem.session_id == session_id, DraftItem.review_state == "pending",
                                                   DraftItem.merged_into_id.is_(None)).order_by(DraftItem.created_at)).all()
        cards = [_card(s, d, sent) for d in drafts]
        from .suggestions import _pending_markers

        incoming = len(_pending_markers(s, session_id))
    flow["cards"] = cards
    flow["incoming"] = incoming  # screenshots that become cards once their speech is transcribed
    flow["approved_unsent"] = flow["counts"].get("approved", 0)
    return flow


def waiting_count(session_id: Optional[str], project_id: Optional[str] = None) -> int:
    """Cards waiting for review, for toasts and the tray."""
    with read_session() as s:
        q = select(DraftItem.id).where(DraftItem.review_state == "pending", DraftItem.merged_into_id.is_(None))
        if session_id:
            q = q.where(DraftItem.session_id == session_id)
        elif project_id:
            q = q.where(DraftItem.project_id == project_id)
        return len(s.scalars(q).all())


# ------------------------------------------------------------------ actions (each returns the draft's session)
def approve(draft_id: str) -> None:
    rv.approve([draft_id])
    agents.refresh_name(_session_of(draft_id))


def dismiss(draft_id: str) -> None:
    rv.dismiss(draft_id)
    agents.refresh_name(_session_of(draft_id))


def undo(draft_id: str) -> None:
    """Undo the last decision on a card: a dismissal is restored; an approval that hasn't been sent is taken back."""
    with write_session() as s:
        d = s.get(DraftItem, draft_id)
        if d is None:
            raise rv.ReviewError("Card not found")
        if d.review_state == "approved" and d.work_item_id:
            w = s.get(WorkItem, d.work_item_id)
            if w is not None and w.agent_state is not None:
                raise rv.ReviewError("That task has already been sent, so it can't be undone here.")
            if w is not None:
                s.delete(w)
            d.work_item_id = None
            d.review_state = "pending"
            d.version += 1
            session_id = d.session_id
        else:
            session_id = d.session_id
    rv.restore(draft_id)
    agents.refresh_name(session_id)


def edit(draft_id: str, title: str, description: str) -> dict:
    out = rv.update_draft(draft_id, {"title": title, "description": description, "needs_context": False})
    agents.refresh_name(out.get("session_id"))
    return out


def _session_of(draft_id: str) -> Optional[str]:
    with read_session() as s:
        d = s.get(DraftItem, draft_id)
        return d.session_id if d else None
