"""Storage usage and attachment retention. Item text and transcripts are retained."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from ..config import paths
from ..db import read_session, write_session
from ..models import (AudioChunk, Capture, DraftItem, EvidenceLink, Note, Session,
                      TranscriptSegment, TranscriptionJob, WorkItem, utcnow)
from ..settings_store import get_settings
from ..storage import (atomic_write_bytes, audio_path, dir_size, media_path,
                       remove_tree, shared_cache_path)

log = logging.getLogger(__name__)
PLACEHOLDER_VERSION = 2


def placeholder_url(status: str) -> str:
    return '/api/media/retention/' + ('deleted' if status == 'deleted' else 'done') + f'?v={PLACEHOLDER_VERSION}'


def placeholder_path(status: str) -> Path:
    """High-resolution glass PNGs using the same vector icons as swipe decisions."""
    from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
    import numpy as np
    from ..desktop.icons import paint_decision_icon

    deleted = status == 'deleted'
    kind = 'deleted' if deleted else 'done'
    target = media_path(f'_retention/{kind}-v{PLACEHOLDER_VERSION}.png')
    if target.is_file():
        return target
    # Render vectors at double the output resolution for smooth translucent rims.
    image = QImage(3200, 2000, QImage.Format.Format_RGBA64_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(4, 4)
    rect = QRectF(12, 12, 776, 476)
    shape = QPainterPath()
    shape.addRoundedRect(rect, 30, 30)
    tint = (198, 62, 79) if deleted else (30, 148, 101)
    glass = QLinearGradient(120, 12, 630, 488)
    glass.setColorAt(0, QColor(*tint, 28))
    glass.setColorAt(.45, QColor(*tint, 48))
    glass.setColorAt(1, QColor(*tint, 90))
    painter.fillPath(shape, glass)
    painter.save()
    painter.setClipPath(shape)
    reflection = QLinearGradient(60, 0, 540, 500)
    reflection.setColorAt(0, QColor(255, 255, 255, 90))
    reflection.setColorAt(.48, QColor(255, 255, 255, 12))
    reflection.setColorAt(1, QColor(255, 255, 255, 0))
    painter.fillRect(rect, reflection)
    painter.restore()
    rim = QLinearGradient(120, 0, 650, 500)
    rim.setColorAt(0, QColor(255, 255, 255, 235))
    rim.setColorAt(.48, QColor(255, 255, 255, 110))
    rim.setColorAt(1, QColor(*tint, 95))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(rim, 1.4))
    painter.drawPath(shape)
    painter.setPen(QPen(QColor(255, 255, 255, 75), .8))
    painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 27, 27)
    paint_decision_icon(painter, QRectF(316, 166, 168, 168), kind, monochrome=QColor(*tint, 235))
    painter.end()
    image = image.scaled(1600, 1000, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    # Preserve the smooth glass gradient when encoding 8-bit PNGs; a single
    # fractional-bit dither prevents visible bands in translucent areas.
    image = image.convertToFormat(QImage.Format.Format_RGBA64)
    precise = np.frombuffer(image.constBits(), dtype=np.uint16).reshape(1000, 1600, 4)
    noise = np.random.default_rng(PLACEHOLDER_VERSION).random((1000, 1600, 1), dtype=np.float32)
    rgba = np.floor(precise.astype(np.float32) / 257 + noise).clip(0, 255).astype(np.uint8)
    image = QImage(rgba.data, 1600, 1000, rgba.strides[0], QImage.Format.Format_RGBA8888)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise OSError('Could not render glass placeholder')
    atomic_write_bytes(target, bytes(buffer.data()))
    return target


def _unlink(path, base) -> None:
    # Resolve every target, including cached copies, before removing a file.
    if base.resolve() not in path.resolve().parents:
        raise ValueError('Attachment path escapes application storage')
    path.unlink(missing_ok=True)


def cleanup_item_media() -> dict:
    """Run on launch and periodically. Repeated runs retry any failed file removals.

    Files shared with retained items or pending drafts stay until their last owner
    expires. No text records or evidence links are deleted.
    """
    from . import image_cards

    prefs = get_settings().retention
    now = utcnow()
    removed = 0
    # PNG generation reads the same files; never remove them mid-render.
    with image_cards._lock:
        with write_session() as s:
            items = {w.id: w for w in s.scalars(select(WorkItem)).all()}
            for w in items.values():
                days = {'deleted': prefs.deleted_days, 'done': prefs.done_days, 'wont_do': prefs.done_days}.get(w.work_status)
                since = w.status_changed_at or w.completed_at or w.human_updated_at or w.updated_at or w.created_at
                if not w.media_removed_at and days is not None and since <= now - timedelta(days=days):
                    w.media_removed_at = now
                    w.version += 1  # Invalidate UI details without changing human chronology or syncing.
                    removed += 1
            expired = {w.id for w in items.values() if w.media_removed_at}
            if not expired:
                return {'items': 0}
            pending = {d.id for d in s.scalars(select(DraftItem).where(DraftItem.review_state == 'pending')).all()}
            owners = defaultdict(set)
            for w in items.values():
                for aid in w.shared_attachment_ids or []:
                    owners['capture', aid].add(w.id)
            links = s.scalars(select(EvidenceLink)).all()
            for link in links:
                owner = link.work_item_id or ('pending' if link.draft_id in pending else None)
                if owner:
                    for kind in ('capture', 'note', 'segment'):
                        ident = getattr(link, kind + '_id')
                        if ident:
                            owners[kind, ident].add(owner)
            notes = s.scalars(select(Note)).all()
            for note in notes:
                # Voice notes can belong through their screenshot as well as explicit evidence.
                if note.capture_id:
                    owners['capture', note.capture_id].update(owners['note', note.id])
            for note in notes:
                if note.capture_id:
                    owners['note', note.id].update(owners['capture', note.capture_id])

            def eligible(kind, ident):
                refs = owners[kind, ident]
                return bool(refs) and refs <= expired

            captures = s.scalars(select(Capture)).all()
            retired_captures = set()
            for c in captures:
                if not eligible('capture', c.id):
                    continue
                try:
                    current, thumb = media_path(c.image_rel_path), media_path(c.thumb_rel_path)
                    candidates = {current, thumb}
                    # Revisions, edited originals and thumbnails all carry this capture's UUID.
                    if re.fullmatch(r'[0-9a-f-]{36}', c.id):
                        candidates.add(current.parent / f'{c.id}.png')
                        candidates.add(current.parent / f'{c.id}.jpg')
                        candidates.update(current.parent.glob(f'{c.id}_*.png'))
                        candidates.update(current.parent.glob(f'{c.id}_*.jpg'))
                    for path in candidates:
                        _unlink(path, paths().media)
                    c.media_removed_at = c.media_removed_at or now
                    retired_captures.add(c.id)
                except (OSError, ValueError):
                    log.exception('Will retry removing screenshot %s', c.id)
            for note in notes:
                if note.audio_rel_path and eligible('note', note.id):
                    try:
                        _unlink(audio_path(note.audio_rel_path), paths().audio)
                        note.audio_rel_path = None
                    except (OSError, ValueError):
                        log.exception('Will retry removing voice note %s', note.id)

            segments = s.scalars(select(TranscriptSegment)).all()
            for seg in segments:
                if seg.chunk_id:
                    owners['chunk', seg.chunk_id].update(owners['segment', seg.id])
            sessions = {sess.id: sess for sess in s.scalars(select(Session)).all()}
            session_items = defaultdict(set)
            for w in items.values():
                session_items[w.session_id].add(w.id)
            pending_sessions = set(s.scalars(select(DraftItem.session_id).where(DraftItem.id.in_(pending))).all()) if pending else set()
            jobs = {job.chunk_id: job for job in s.scalars(select(TranscriptionJob)).all()}
            chunks = s.scalars(select(AudioChunk)).all()
            for chunk in chunks:
                sess = sessions.get(chunk.session_id)
                job = jobs.get(chunk.id)
                if not sess or sess.state in ('active', 'paused') or chunk.state == 'recording' or (job and job.state == 'running'):
                    continue
                whole_session = bool(session_items[sess.id]) and session_items[sess.id] <= expired and sess.id not in pending_sessions
                if not eligible('chunk', chunk.id) and not (whole_session and not (owners['chunk', chunk.id] - expired)):
                    continue
                try:
                    _unlink(audio_path(chunk.rel_path), paths().audio)
                    chunk.state = 'deleted'
                    if job and job.state != 'done':
                        job.state = 'failed'
                        job.error = 'Recording removed by attachment cleanup. Existing transcript text is retained.'
                except (OSError, ValueError):
                    log.exception('Will retry removing recording %s', chunk.id)
            for sess in sessions.values():
                session_chunks = [c for c in chunks if c.session_id == sess.id]
                if session_chunks and all(c.state == 'deleted' for c in session_chunks):
                    sess.raw_audio_deleted = True
            # Shared downloads are local copies; do not change the published server history.
            retired_shared = {aid for w in items.values() if w.id in expired for aid in (w.shared_attachment_ids or [])}
            retained_shared = {aid for w in items.values() if w.id not in expired for aid in (w.shared_attachment_ids or [])}
            for aid in retired_shared - retained_shared:
                for suffix in ('', '.type'):
                    try:
                        _unlink(shared_cache_path(aid + suffix), paths().shared_cache)
                    except (OSError, ValueError):
                        log.exception('Will retry removing shared attachment %s', aid)

        # Remove resized AI copies only after their source is safely retired.
        for path in (paths().exports / 'ai').rglob('*.jpg'):
            if path.name.split('_', 1)[0] in retired_captures:
                try:
                    _unlink(path, paths().exports)
                except OSError:
                    log.exception('Will retry removing cached screenshot %s', path)
        # Replace application-generated cards (including old export copies), preserving their text.
        from .export import _collect
        retired_items, _ = _collect(sorted(expired), True)
        old_folders = defaultdict(set)
        for root in (paths().root / 'images', paths().exports / 'cards', paths().exports / 'images'):
            for path in root.rglob('*.png'):
                iid = image_cards._owned_item(path)
                if iid in expired:
                    old_folders[iid].add(path.parent)
        for item in retired_items:
            folders = {image_cards.output_folder(item['project_id'], item['work_status']), *old_folders[item['id']]}
            for folder in folders:
                try:
                    image_cards._write_item(item, folder)
                except (OSError, ValueError):
                    log.exception('Will retry replacing retired PNG card %s', item['id'])
    if removed:
        from ..events import hooks
        hooks.emit_state()
    return {'items': removed}


def usage() -> dict:
    p = paths()
    db = sum(f.stat().st_size for f in p.root.glob("checkpoint.db*") if f.is_file())
    return {
        "data_dir": str(p.root),
        "database_bytes": db,
        "screenshots_bytes": dir_size(p.media),
        "audio_bytes": dir_size(p.audio),
        "transcripts_bytes": dir_size(p.transcripts),
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
        "transcript_bytes": dir_size(paths().transcripts / session_id),
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
    from .transcripts import refresh_session_transcript
    refresh_session_transcript(session_id)
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
    from .transcripts import remove_session_transcripts
    remove_session_transcripts(session_id)
    return {"ok": True, "kept_items": impact["approved_items"]}
