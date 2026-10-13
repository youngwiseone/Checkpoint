"""Settle automatic observations before exposing them in the task deck."""
import re
import time
import logging
from difflib import SequenceMatcher

from sqlalchemy import select

from ..db import read_session, write_session
from ..models import AudioChunk, AudioSource, Capture, EvidenceLink, Session, TranscriptionJob, TranscriptSegment, WorkItem, utcnow
from ..settings_store import get_settings
from ..workers import HEAVY_LOCK
from .capture import guess_type, provisional_title

CONFIRM = ('Does the context confirm the original remark is a real observed on-screen problem that still needs action? '
           'Answer no for speculation, questions, expected behaviour, later retractions, or a problem the speaker says is resolved. '
           'Use later corrections to interpret the original remark.')
_provider = None
_retry_after = {}
log = logging.getLogger(__name__)


def same_issue(a, b):
    def normalized(text):
        return re.findall(r"[\w']+", text.lower())
    aa, bb = normalized(a), normalized(b)
    if len(aa) < 4 or len(bb) < 4:
        return aa == bb and bool(aa)
    # A changed number/negation can change the issue's scope.
    scope = lambda words: {w for w in words if w.isdigit() or w in ('not', 'never', 'only', "doesn't", "can't")}
    if scope(aa) != scope(bb):
        return False
    return SequenceMatcher(None, ' '.join(aa), ' '.join(bb)).ratio() >= .90


def suppressed(project_id, session_id, remark, window_title=''):
    if not session_id:
        return False
    with read_session() as s:
        rows = s.execute(select(WorkItem, Capture).join(EvidenceLink, EvidenceLink.work_item_id == WorkItem.id)
            .join(Capture, Capture.id == EvidenceLink.capture_id).where(
                WorkItem.project_id == project_id, WorkItem.feedback_reason.in_(('expected', 'misheard')),
                WorkItem.work_status == 'wont_do', Capture.session_id == session_id)).all()
        return any(c.window_title == window_title and same_issue(c.reason or w.description or w.title, remark)
                   for w, c in rows)


def confirm(remark, context):
    global _provider
    st = get_settings()
    from ..extraction.ollama import OllamaProvider
    if _provider is None or _provider.base_url != st.ai.base_url.rstrip('/'):
        _provider = OllamaProvider(st.ai.base_url, timeout=120, max_retries=1)
    return _provider.noul(st.auto_capture.model, {'original_remark': remark, 'later_context': context}, CONFIRM) >= st.auto_capture.threshold


def settle():
    with read_session() as s:
        linked = select(EvidenceLink.capture_id).where(EvidenceLink.work_item_id.is_not(None), EvidenceLink.capture_id.is_not(None))
        captures = s.scalars(select(Capture).where(Capture.trigger == 'auto', Capture.status == 'marker',
                                                 Capture.id.not_in(linked))).all()
    for c in captures:
        if time.monotonic() < _retry_after.get(c.id, 0):
            continue
        with read_session() as s:
            sess = s.get(Session, c.session_id)
            mic = select(AudioSource.id).where(AudioSource.session_id == c.session_id, AudioSource.kind == 'mic')
            pending = s.scalar(select(TranscriptionJob.id).join(AudioChunk, AudioChunk.id == TranscriptionJob.chunk_id).where(
                AudioChunk.source_id.in_(mic), AudioChunk.start_offset_ms <= c.offset_ms + 20000,
                AudioChunk.start_offset_ms + AudioChunk.duration_ms >= c.offset_ms - 5000,
                TranscriptionJob.state.in_(('queued', 'running'))).limit(1))
            if pending:
                continue
            segments = s.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == c.session_id,
                TranscriptSegment.source_id.in_(mic), TranscriptSegment.end_ms >= c.offset_ms - 5000,
                TranscriptSegment.start_ms <= c.offset_ms + 20000).order_by(TranscriptSegment.start_ms)).all()
            latest = s.scalar(select(TranscriptSegment.end_ms).where(TranscriptSegment.session_id == c.session_id,
                TranscriptSegment.source_id.in_(mic)).order_by(TranscriptSegment.end_ms.desc())) or 0
            # A quiet tail still settles; background transcription has a minute to catch up.
            if sess and sess.state in ('active', 'paused') and latest < c.offset_ms + 15000 and (utcnow() - c.created_at).total_seconds() < 60:
                continue
        if not segments or suppressed(c.project_id, c.session_id, c.reason or '', c.window_title):
            if segments:
                with write_session() as s:
                    s.get(Capture, c.id).status = 'discarded'
            continue
        context = ' '.join((g.corrected_text or g.text).strip() for g in segments)
        if not HEAVY_LOCK.acquire(timeout=.1):
            return
        try:
            accepted = confirm(c.reason or context, context)
        except Exception as error:
            # Keep evidence for a retry; a model outage cannot turn a guess into a task.
            _retry_after[c.id] = time.monotonic() + 60
            log.warning('Could not settle automatic capture %s: %s', c.id, error)
            continue
        finally:
            HEAVY_LOCK.release()
        _retry_after.pop(c.id, None)
        if not accepted:
            with write_session() as s:
                s.get(Capture, c.id).status = 'discarded'
            continue
        with write_session() as s:
            cap = s.get(Capture, c.id)
            if cap.status != 'marker' or s.scalars(select(EvidenceLink.id).where(
                    EvidenceLink.capture_id == cap.id, EvidenceLink.work_item_id.is_not(None))).first():
                continue
            # Only compare to other automatic observations in this window and session.
            candidates = s.execute(select(WorkItem, Capture).join(EvidenceLink, EvidenceLink.work_item_id == WorkItem.id)
                .join(Capture, Capture.id == EvidenceLink.capture_id).where(WorkItem.project_id == cap.project_id,
                    WorkItem.work_status.in_(('open', 'in_progress')), Capture.session_id == cap.session_id,
                    Capture.window_title == cap.window_title, Capture.trigger == 'auto')).all()
            w = next((w for w, previous in candidates if same_issue(previous.reason or '', cap.reason or '')), None)
            if w is None:
                w = WorkItem(project_id=cap.project_id, session_id=cap.session_id, type=guess_type(cap.reason or context),
                    title=provisional_title(cap.reason or context), description=context, human_updated_at=cap.taken_at)
                s.add(w)
                s.flush()
            s.add(EvidenceLink(work_item_id=w.id, capture_id=cap.id, confidence='direct', attached_by='system'))
            existing = set(s.scalars(select(EvidenceLink.segment_id).where(EvidenceLink.work_item_id == w.id)).all())
            for seg in segments:
                if seg.id not in existing:
                    s.add(EvidenceLink(work_item_id=w.id, segment_id=seg.id, confidence='direct', attached_by='system'))
            cap.status = 'saved'
            w.version += 1
