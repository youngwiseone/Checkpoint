"""Storage usage, raw-audio deletion and session deletion (never silent)."""

from __future__ import annotations

from sqlalchemy import func, select

from ..config import paths
from ..db import read_session, write_session
from ..models import AudioChunk, Capture, DraftItem, EvidenceLink, Session, WorkItem
from ..storage import dir_size, media_path, remove_tree


def usage() -> dict:
    p = paths()
    db = sum(f.stat().st_size for f in p.root.glob("checkpoint.db*") if f.is_file())
    return {
        "data_dir": str(p.root),
        "database_bytes": db,
        "screenshots_bytes": dir_size(p.media),
        "audio_bytes": dir_size(p.audio),
        "models_bytes": dir_size(p.models),
        "shared_cache_bytes": dir_size(p.shared_cache),
    }


def session_impact(session_id: str) -> dict:
    with read_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise ValueError("Session not found")
        cap_ids = s.scalars(select(Capture.id).where(Capture.session_id == session_id)).all()
        items_with_evidence = s.scalars(
            select(WorkItem.id).join(EvidenceLink, EvidenceLink.work_item_id == WorkItem.id)
            .where(EvidenceLink.capture_id.in_(cap_ids) if cap_ids else False).distinct()
        ).all() if cap_ids else []
        approved = s.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.session_id == session_id)) or 0
        chunks = s.scalar(select(func.count()).select_from(AudioChunk).where(AudioChunk.session_id == session_id, AudioChunk.state != "deleted")) or 0
        pending = s.scalar(select(func.count()).select_from(DraftItem).where(DraftItem.session_id == session_id, DraftItem.review_state == "pending")) or 0
    return {
        "session_id": session_id,
        "audio_bytes": dir_size(paths().audio / session_id),
        "screenshot_bytes": dir_size(paths().media / session_id),
        "audio_chunks": chunks,
        "captures": len(cap_ids),
        "approved_items": approved,
        "items_using_screenshots": len(items_with_evidence),
        "pending_cards": pending,
    }


def delete_raw_audio(session_id: str) -> dict:
    """Keeps transcripts, cards and items. Replay of those segments becomes unavailable."""
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise ValueError("Session not found")
        if sess.state in ("active", "paused"):
            raise ValueError("End the session first")
        for c in s.scalars(select(AudioChunk).where(AudioChunk.session_id == session_id)).all():
            c.state = "deleted"
        sess.raw_audio_deleted = True
    remove_tree(paths().audio / session_id, paths().audio)
    return {"ok": True}


def delete_session(session_id: str, confirm_items_lose_evidence: bool) -> dict:
    impact = session_impact(session_id)
    if impact["items_using_screenshots"] and not confirm_items_lose_evidence:
        raise ValueError(
            f"{impact['items_using_screenshots']} approved item(s) use screenshots from this session. "
            "Confirm to keep those items without their screenshots and quotes."
        )
    with write_session() as s:
        sess = s.get(Session, session_id)
        if sess is None:
            raise ValueError("Session not found")
        if sess.state in ("active", "paused"):
            raise ValueError("End the session first")
        s.delete(sess)
    remove_tree(paths().audio / session_id, paths().audio)
    remove_tree(paths().media / session_id, paths().media)
    return {"ok": True, "kept_items": impact["approved_items"]}
