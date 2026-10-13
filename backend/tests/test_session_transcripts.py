"""Full speech exports must include audio without any screenshots or task links."""
from types import SimpleNamespace

import numpy as np
import pytest


def recording(mode='live', state='active', title='Delivery review'):
    from checkpoint.db import write_session
    from checkpoint.models import Session, AudioSource, AudioChunk, TranscriptionJob
    from checkpoint.services.capture import default_project_id
    with write_session() as db:
        session = Session(project_id=default_project_id(), title=title, state=state, transcription_mode=mode)
        db.add(session)
        db.flush()
        sources = []
        for kind, label in (('mic', 'Me'), ('loopback', 'Computer')):
            source = AudioSource(session_id=session.id, kind=kind, label=label)
            db.add(source)
            db.flush()
            sources.append(source)
        chunks, jobs = [], []
        for seq, source in enumerate(sources):
            chunk = AudioChunk(session_id=session.id, source_id=source.id, seq=1,
                               rel_path=f'{source.kind}.wav', start_offset_ms=seq * 10000,
                               duration_ms=10000, sample_rate=16000, channels=1, state='complete')
            db.add(chunk)
            db.flush()
            job = TranscriptionJob(session_id=session.id, chunk_id=chunk.id)
            db.add(job)
            db.flush()
            chunks.append(chunk.id)
            jobs.append(job.id)
        return session.id, chunks, jobs, [source.id for source in sources]


def fake_engine(monkeypatch, phrases):
    from checkpoint.transcription import worker
    monkeypatch.setattr(worker, 'load_chunk_audio', lambda chunk: np.zeros(16000, dtype=np.float32))
    engine = SimpleNamespace(ensure=lambda *args: None, fallback_message=None,
        transcribe=lambda *args, **kwargs: [{'start': 0., 'end': .8, 'text': next(phrases),
                                           'confidence': .9, 'low': False}])
    instance = worker.TranscriptionWorker()
    instance.engine = engine
    return instance


def test_live_exports_every_source_without_screenshots_and_finalizes(app_env, monkeypatch):
    from checkpoint.db import read_session, write_session
    from checkpoint.models import Session, Capture, TranscriptSegment, utcnow
    from checkpoint.services.transcripts import write_session_transcript
    from sqlalchemy import select
    sid, chunks, jobs, sources = recording()
    worker = fake_engine(monkeypatch, iter(['Change the vehicle label.', 'Also check the checkout totals.']))
    assert worker.step()
    path = write_session_transcript(sid)
    assert path.name == 'Delivery review.txt'
    assert 'Change the vehicle label.' in path.read_text(encoding='utf-8')
    assert 'In progress' in path.read_text(encoding='utf-8')
    assert worker.step()
    with write_session() as db:
        session = db.get(Session, sid)
        session.state, session.ended_at = 'ended', utcnow()
    text = write_session_transcript(sid).read_text(encoding='utf-8')
    assert 'Transcription: Complete' in text
    assert text.index('Change the vehicle label.') < text.index('Also check the checkout totals.')
    assert 'Microphone (Me)' in text and 'Computer audio (Computer)' in text
    with read_session() as db:
        assert db.scalars(select(Capture).where(Capture.session_id == sid)).all() == []
        assert len(db.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == sid)).all()) == 2


def test_after_mode_waits_for_end_and_off_remains_off(app_env, monkeypatch):
    from checkpoint.db import write_session
    from checkpoint.models import Session
    from checkpoint.services.transcripts import write_session_transcript
    sid, chunks, jobs, sources = recording(mode='after')
    worker = fake_engine(monkeypatch, iter(['One', 'Two']))
    assert not worker.step()
    assert 'Waiting until recording ends' in write_session_transcript(sid).read_text(encoding='utf-8')
    with write_session() as db:
        db.get(Session, sid).state = 'ended'
    assert worker.step() and worker.step()
    off, *_ = recording(mode='off')
    assert write_session_transcript(off) is None
    assert not worker.step()


def test_export_errors_do_not_retry_already_transcribed_audio(app_env, monkeypatch):
    from checkpoint.db import read_session
    from checkpoint.models import TranscriptionJob
    from checkpoint.services import transcripts
    sid, chunks, jobs, sources = recording()
    monkeypatch.setattr(transcripts, 'write_session_transcript', lambda sid: (_ for _ in ()).throw(OSError('Disk full')))
    worker = fake_engine(monkeypatch, iter(['Words remain safe.']))
    assert worker.step()
    with read_session() as db:
        job = db.get(TranscriptionJob, jobs[0])
        assert job.state == 'done' and job.attempts == 1 and job.error is None


def test_safe_filenames_duplicate_session_names_and_corrections(app_env):
    from checkpoint.config import paths
    from checkpoint.db import write_session
    from checkpoint.models import TranscriptSegment, TranscriptionJob
    from checkpoint.services.transcripts import write_session_transcript, sync_session_transcripts
    title = '../Planning: / Q&A? café'
    first, chunks, jobs, sources = recording(state='ended', title=title)
    second, *_ = recording(state='ended', title=title)
    with write_session() as db:
        db.add(TranscriptSegment(id='speech', session_id=first, source_id=sources[0], chunk_id=chunks[0],
                                 start_ms=100, end_ms=500, text='Original wording', corrected_text='Corrected café wording'))
        db.get(TranscriptionJob, jobs[0]).state = 'done'
        db.get(TranscriptionJob, jobs[1]).state = 'failed'
        db.get(TranscriptionJob, jobs[1]).error = 'Audio unavailable'
    assert sync_session_transcripts() == 2
    path = write_session_transcript(first)
    other = write_session_transcript(second)
    assert path != other and path.is_relative_to(paths().transcripts)
    assert not any(char in path.name for char in '<>:"/\\|?*')
    text = path.read_text(encoding='utf-8')
    assert 'Corrected café wording' in text and 'Original wording' not in text
    assert 'Transcription: Incomplete' in text and 'Audio unavailable' in text
    modified = path.stat().st_mtime_ns
    assert write_session_transcript(first).stat().st_mtime_ns == modified


def test_pause_does_not_override_transcription_choice(app_env, monkeypatch):
    from checkpoint.settings_store import update_settings
    from checkpoint.services.transcripts import write_session_transcript
    sid, *_ = recording()
    update_settings({'transcription': {'paused': True}})
    worker = fake_engine(monkeypatch, iter([]))
    assert not worker.step()
    assert 'Transcription: Paused' in write_session_transcript(sid).read_text(encoding='utf-8')


def test_audio_deletion_keeps_words_and_session_deletion_removes_export(app_env, monkeypatch):
    from checkpoint.services.retention import delete_raw_audio, delete_session, session_impact, usage
    from checkpoint.services.transcripts import write_session_transcript
    sid, *_ = recording(state='ended')
    worker = fake_engine(monkeypatch, iter(['Keep this spoken task.', 'And this one.']))
    assert worker.step() and worker.step()
    path = write_session_transcript(sid)
    assert session_impact(sid)['transcript_bytes'] == usage()['transcripts_bytes'] == path.stat().st_size
    delete_raw_audio(sid)
    text = path.read_text(encoding='utf-8')
    assert 'Keep this spoken task.' in text and 'And this one.' in text
    assert 'Transcription: Complete' in text
    delete_session(sid, True)
    assert not path.parent.exists()


def test_deleted_audio_without_transcription_is_reported_as_incomplete(app_env):
    from checkpoint.services.retention import delete_raw_audio
    from checkpoint.services.transcripts import write_session_transcript
    sid, *_ = recording(state='ended')
    path = write_session_transcript(sid)
    delete_raw_audio(sid)
    text = path.read_text(encoding='utf-8')
    assert 'Transcription: Incomplete' in text and '2 unavailable audio chunks' in text
