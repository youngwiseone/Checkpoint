"""Retained note recordings and short replay clips from session evidence."""
import io
import wave

from ..db import read_session, write_session
from ..models import AudioChunk, Capture, EvidenceLink, Note, TranscriptSegment, WorkItem, new_id
from ..storage import atomic_write_bytes, audio_path


def retain(capture_id, audio, text=''):
    if not audio:
        return None
    from .inbox import ensure_capture
    iid = ensure_capture(capture_id)
    with write_session() as s:
        c = s.get(Capture, capture_id)
        nid = new_id()
        rel = f'{c.session_id or "notes"}/{nid}.wav'
        atomic_write_bytes(audio_path(rel), audio)
        n = Note(id=nid, project_id=c.project_id, session_id=c.session_id, capture_id=c.id,
                 text=text, kind='voice', taken_at=c.taken_at, offset_ms=c.offset_ms, audio_rel_path=rel)
        s.add(n)
        s.flush()
        s.add(EvidenceLink(work_item_id=iid, note_id=n.id, confidence='direct', attached_by='user'))
        s.get(WorkItem, iid).version += 1
    return nid


def finish_note(note_id, text, words):
    with write_session() as s:
        note = s.get(Note, note_id)
        if note:
            note.text, note.speech_words = text, words


def segment_clip(segment_id, pad_ms=700, end_segment_id=None):
    with read_session() as s:
        seg = s.get(TranscriptSegment, segment_id)
        chunk = s.get(AudioChunk, seg.chunk_id) if seg and seg.chunk_id else None
        if not chunk or chunk.state in ('deleted', 'missing'):
            raise ValueError('The recording is no longer available.')
        path = audio_path(chunk.rel_path)
        start_ms, end_ms = seg.start_ms - chunk.start_offset_ms, seg.end_ms - chunk.start_offset_ms
        if end_segment_id:
            end_seg = s.get(TranscriptSegment, end_segment_id)
            if end_seg and end_seg.chunk_id == chunk.id:
                end_ms = end_seg.end_ms - chunk.start_offset_ms
    with wave.open(str(path), 'rb') as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        start = min(wav.getnframes(), max(0, int((start_ms - pad_ms) * rate / 1000)))
        end = min(wav.getnframes(), int((end_ms + pad_ms) * rate / 1000))
        wav.setpos(start)
        frames = wav.readframes(max(0, end - start))
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setframerate(rate)
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.writeframes(frames)
    return output.getvalue()


def playable_evidence(evidence):
    entries = [e for e in evidence if e.get('audio_url')]
    voice = [e for e in entries if e['kind'] == 'note']
    if voice:
        return voice
    grouped = []
    for entry in entries:
        previous = grouped[-1] if grouped else None
        if previous and entry.get('chunk_id') and previous.get('chunk_id') == entry['chunk_id'] and entry['offset_ms'] - previous['end_ms'] < 1500:
            previous['end_segment_id'] = entry['segment_id']
            previous['end_ms'] = max(previous['end_ms'], entry['end_ms'])
        else:
            grouped.append(dict(entry))
    return grouped


def evidence_audio(entry):
    if entry['kind'] == 'segment':
        return segment_clip(entry['segment_id'], end_segment_id=entry.get('end_segment_id'))
    with read_session() as s:
        note = s.get(Note, entry['note_id'])
        if not note or not note.audio_rel_path:
            raise ValueError('The recording is no longer available.')
        data = audio_path(note.audio_rel_path).read_bytes()
    if entry.get('start_s') is not None:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            start = max(0, min(wav.getnframes(), int((entry['start_s'] - .5) * rate)))
            end = max(start, min(wav.getnframes(), int((entry['end_s'] + .5) * rate)))
            wav.setpos(start)
            frames = wav.readframes(end - start)
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setframerate(rate)
            wav.setnchannels(channels)
            wav.setsampwidth(width)
            wav.writeframes(frames)
        return output.getvalue()
    return data
