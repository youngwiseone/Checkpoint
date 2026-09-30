"""Transcription worker: processes persisted jobs, one audio chunk at a time.

Recording never waits for this worker. Jobs survive restarts (running → queued on
launch). Segments and the job's `done` state are written in one transaction with
deterministic IDs, so a retry can never duplicate transcript text.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import numpy as np
from sqlalchemy import and_, select

from ..config import paths
from ..db import read_session, write_session
from ..events import notices
from ..models import AudioChunk, AudioSource, Project, Session, TranscriptionJob, TranscriptSegment
from ..settings_store import get_settings
from ..workers import HEAVY_LOCK, Worker
from .engine import ModelMissing, WhisperEngine

log = logging.getLogger(__name__)
SEG_NS = uuid.UUID("6f1d3c52-7d0e-4c3b-9a55-3e8a2b1f0c11")
MAX_ATTEMPTS = 3


def recover_jobs() -> int:
    with write_session() as s:
        jobs = s.scalars(select(TranscriptionJob).where(TranscriptionJob.state == "running")).all()
        for j in jobs:
            j.state = "queued"
        return len(jobs)


def load_chunk_audio(chunk: AudioChunk):  # noqa: ANN201
    from faster_whisper import decode_audio

    p = paths().audio / chunk.rel_path
    return decode_audio(str(p), sampling_rate=16000)


class TranscriptionWorker(Worker):
    name = "transcription"
    idle_sleep = 2.0

    def __init__(self) -> None:
        super().__init__()
        self.engine = WhisperEngine()
        self.current_job: Optional[str] = None
        self.model_error: Optional[str] = None
        self._missing_model_notified = False

    # Jobs are eligible when the session allows it: live sessions any time,
    # "after" sessions once ended, "off" only after the user asks.
    def _next_job(self) -> Optional[tuple[str, str]]:
        with read_session() as s:
            q = (
                select(TranscriptionJob.id, TranscriptionJob.chunk_id)
                .join(Session, Session.id == TranscriptionJob.session_id)
                .join(AudioChunk, AudioChunk.id == TranscriptionJob.chunk_id)
                .where(TranscriptionJob.state == "queued")
                .where(AudioChunk.state.in_(("complete", "recovered")))
                .where(
                    (Session.transcription_mode == "live")
                    | and_(Session.transcription_mode == "after", Session.state.in_(("ended", "interrupted")))
                )
                .order_by(Session.started_at, AudioChunk.start_offset_ms)
                .limit(1)
            )
            row = s.execute(q).first()
            return (row[0], row[1]) if row else None

    def step(self) -> bool:
        st = get_settings().transcription
        if st.paused:
            return False
        nxt = self._next_job()
        if not nxt:
            # Release model memory when idle for a while is not needed for base models; keep it simple.
            return False
        job_id, chunk_id = nxt
        try:
            self.engine.ensure(st.model, st.device, st.compute_type, st.cpu_threads)
            self.model_error = None
            self._missing_model_notified = False
        except ModelMissing as e:
            self.model_error = str(e)
            if not self._missing_model_notified:
                notices.push("warning", str(e), "transcription")
                self._missing_model_notified = True
            return False
        except Exception as e:  # noqa: BLE001
            self.model_error = f"Couldn't load the transcription model: {e}"
            return False
        with HEAVY_LOCK:
            self._run_job(job_id, chunk_id, st)
        return True

    def _run_job(self, job_id: str, chunk_id: str, st) -> None:  # noqa: ANN001
        self.busy = True
        self.current_job = job_id
        try:
            with write_session() as s:
                job = s.get(TranscriptionJob, job_id)
                if job is None or job.state != "queued":
                    return
                job.state = "running"
                job.attempts += 1
                job.model = st.model
            with read_session() as s:
                chunk = s.get(AudioChunk, chunk_id)
                session = s.get(Session, chunk.session_id)
                project = s.get(Project, session.project_id)
                prev = None
                if chunk.contiguous_with_prev:
                    prev = s.scalars(
                        select(AudioChunk).where(AudioChunk.source_id == chunk.source_id, AudioChunk.seq == chunk.seq - 1)
                    ).first()
                glossary = (project.glossary or "").strip()
            audio = load_chunk_audio(chunk)
            overlap = 0.0
            if prev is not None and prev.state in ("complete", "recovered"):
                try:
                    prev_audio = load_chunk_audio(prev)
                    n = int(st.overlap_seconds * 16000)
                    tail = prev_audio[-n:]
                    overlap = len(tail) / 16000
                    audio = np.concatenate([tail, audio])
                except Exception:  # noqa: BLE001 - missing previous chunk just means no overlap
                    overlap = 0.0
            prompt = None
            if glossary:
                terms = ", ".join(t.strip() for t in glossary.replace("\n", ",").split(",") if t.strip())[:600]
                prompt = f"Glossary: {terms}."
            segs = self.engine.transcribe(audio, english_only=st.model.endswith(".en"), initial_prompt=prompt)
            rows = []
            for i, seg in enumerate(segs):
                mid = (seg["start"] + seg["end"]) / 2
                # The overlap belongs to the previous chunk; keep only segments centred in this chunk.
                if mid < overlap:
                    continue
                start_ms = chunk.start_offset_ms + int((seg["start"] - overlap) * 1000)
                end_ms = chunk.start_offset_ms + int((seg["end"] - overlap) * 1000)
                seg_id = str(uuid.uuid5(SEG_NS, f"{chunk.id}:{i}:{int(seg['start'] * 1000)}"))
                rows.append(
                    TranscriptSegment(
                        id=seg_id, session_id=chunk.session_id, source_id=chunk.source_id, chunk_id=chunk.id,
                        start_ms=max(chunk.start_offset_ms, start_ms), end_ms=max(start_ms, end_ms), text=seg["text"],
                        confidence=seg["confidence"], low_confidence=seg["low"], model=st.model,
                    )
                )
            with write_session() as s:
                existing = set(s.scalars(select(TranscriptSegment.id).where(TranscriptSegment.chunk_id == chunk.id)).all())
                for r in rows:
                    if r.id not in existing:
                        s.add(r)
                job = s.get(TranscriptionJob, job_id)
                job.state = "done"
                job.error = None
                job.speech_found = bool(rows)
        except Exception as e:  # noqa: BLE001
            log.exception("Transcription job failed")
            with write_session() as s:
                job = s.get(TranscriptionJob, job_id)
                if job is not None:
                    job.error = str(e)[:500]
                    job.state = "failed" if job.attempts >= MAX_ATTEMPTS else "queued"
            if self.engine.fallback_message:
                notices.push("warning", self.engine.fallback_message, "transcription")
        finally:
            self.busy = False
            self.current_job = None


def transcription_status(session_id: Optional[str] = None) -> dict:
    with read_session() as s:
        q = select(TranscriptionJob.state)
        if session_id:
            q = q.where(TranscriptionJob.session_id == session_id)
        states = s.scalars(q).all()
    counts = {k: 0 for k in ("queued", "running", "done", "failed")}
    for st in states:
        counts[st] = counts.get(st, 0) + 1
    return counts


def source_label_map(session_id: str) -> dict[str, dict]:
    with read_session() as s:
        srcs = s.scalars(select(AudioSource).where(AudioSource.session_id == session_id)).all()
        return {x.id: {"kind": x.kind, "label": x.label or ("Microphone" if x.kind == "mic" else "Computer audio")} for x in srcs}
