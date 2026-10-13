"""Full-session UTF-8 transcripts, independent of screenshot/task evidence."""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from sqlalchemy import or_, select

from ..config import paths
from ..db import read_session
from ..events import notices
from ..models import AudioChunk, AudioSource, Project, Session, SessionPause, TranscriptionJob, TranscriptSegment
from ..settings_store import get_settings
from ..storage import atomic_write_bytes, remove_tree

log = logging.getLogger(__name__)
_export_lock = threading.RLock()
_reported_errors = set()


def _filename(value, limit=100):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', value).strip(' .')[:limit].rstrip(' .') or 'Session'
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *('COM' + str(n) for n in range(1, 10)),
                *('LPT' + str(n) for n in range(1, 10))}
    return '_' + name if name.split('.')[0].upper() in reserved else name


def _offset(ms):
    ms = max(0, int(ms or 0))
    seconds, milliseconds = divmod(ms, 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}'


def write_session_transcript(session_id: str) -> Path | None:
    """Atomically refresh every segment from every source, including unlinked speech."""
    with _export_lock:
        with read_session() as db:
            session = db.get(Session, session_id)
            if session is None:
                raise ValueError('Session not found')
            if session.is_demo:
                return None
            segments = db.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == session_id)
                                  .order_by(TranscriptSegment.start_ms, TranscriptSegment.end_ms,
                                            TranscriptSegment.source_id, TranscriptSegment.id)).all()
            if session.transcription_mode == 'off' and not segments:
                return None
            project = db.get(Project, session.project_id)
            sources = {source.id: source for source in db.scalars(
                select(AudioSource).where(AudioSource.session_id == session_id)).all()}
            jobs = db.scalars(select(TranscriptionJob).where(TranscriptionJob.session_id == session_id)).all()
            chunks = db.scalars(select(AudioChunk).where(AudioChunk.session_id == session_id)).all()
            pauses = db.scalars(select(SessionPause).where(SessionPause.session_id == session_id)
                                .order_by(SessionPause.start_offset_ms)).all()
            chunk_states = {chunk.id: chunk.state for chunk in chunks}
            completed_chunks = {job.chunk_id for job in jobs if job.state == 'done'}
            pending = sum(job.state in ('queued', 'running') and chunk_states.get(job.chunk_id) not in ('missing', 'deleted')
                          for job in jobs)
            failed = sum(job.state == 'failed' for job in jobs)
            missing = sum(chunk.state in ('missing', 'deleted') and chunk.id not in completed_chunks for chunk in chunks)
            lines = [f'Session: {session.title}', f'Project: {project.name if project else ""}',
                     f'Started (UTC): {session.started_at:%Y-%m-%d %H:%M:%S}',
                     f'Recording: {session.state}']
            if session.ended_at:
                lines.append(f'Ended (UTC): {session.ended_at:%Y-%m-%d %H:%M:%S}')
            if get_settings().transcription.paused and pending:
                status = f'Paused; {pending} audio chunks waiting'
            elif session.transcription_mode == 'after' and session.state in ('active', 'paused'):
                status = 'Waiting until recording ends'
            elif pending:
                status = f'In progress; {pending} audio chunks remaining'
            elif session.state in ('active', 'paused'):
                status = 'In progress; recording has not ended'
            elif failed or missing:
                status = 'Incomplete'
            else:
                status = 'Complete'
            lines.append('Transcription: ' + status)
            if failed or missing:
                lines.append(f'Gaps: {failed} failed transcription jobs; {missing} unavailable audio chunks')
            for job in jobs:
                if job.state == 'failed' and job.error:
                    lines.append('Transcription error: ' + job.error)
            if pauses:
                lines.extend(['', 'Recording pauses:'])
                for pause in pauses:
                    end = _offset(pause.end_offset_ms) if pause.end_offset_ms is not None else 'ongoing'
                    lines.append(f'[{_offset(pause.start_offset_ms)} - {end}] {pause.reason}')
            lines.extend(['', 'Full transcript:', ''])
            for segment in segments:
                text = segment.corrected_text if segment.corrected_text is not None else segment.text
                if not text.strip():
                    continue
                source = sources.get(segment.source_id)
                kind = 'Microphone' if source and source.kind == 'mic' else 'Computer audio' if source else 'Audio'
                label = f'{kind} ({source.label})' if source and source.label else kind
                lines.extend([f'[{_offset(segment.start_ms)} - {_offset(segment.end_ms)}] {label}', text, ''])
            if not segments:
                lines.append('No transcribed speech yet.' if status != 'Complete' else 'No speech detected.')
            content = '\n'.join(lines).rstrip() + '\n'
            # Per-session directories keep identically named recordings separate.
            folder = paths().transcripts / _filename(session.id, 64)
            target = folder / (_filename(session.title) + '.txt')
        folder.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text(encoding='utf-8') == content:
            return target
        atomic_write_bytes(target, content.encode('utf-8'))
        return target


def refresh_session_transcript(session_id: str) -> Path | None:
    """File errors never invalidate already transcribed audio or stop recording."""
    try:
        result = write_session_transcript(session_id)
        _reported_errors.discard(session_id)
        return result
    except (OSError, ValueError) as error:
        log.warning('Could not save transcript for %s: %s', session_id, error)
        if session_id not in _reported_errors:
            notices.push('warning', f"Couldn't save the text transcript. Its transcribed words are safe in Checkpoint. {error}",
                         'transcription')
            _reported_errors.add(session_id)
        return None


def sync_session_transcripts() -> int:
    """Backfill old recordings and refresh corrections, names, and processing state."""
    with read_session() as db:
        ids = db.scalars(select(Session.id).where(Session.is_demo.is_(False), or_(
            Session.transcription_mode != 'off',
            Session.id.in_(select(TranscriptSegment.session_id))))).all()
    return sum(refresh_session_transcript(session_id) is not None for session_id in ids)


def remove_session_transcripts(session_id: str) -> None:
    """Session deletion also removes text exports, after any in-flight write finishes."""
    with _export_lock:
        remove_tree(paths().transcripts / _filename(session_id, 64), paths().transcripts)
        _reported_errors.discard(session_id)
