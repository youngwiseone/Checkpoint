"""Per-source audio recording to short, independently recoverable WAV chunks.

Design
- The PortAudio callback only enqueues (bytes, arrival time). No disk, DB, AI or
  network work happens on the audio thread.
- A writer thread per source drains the queue, writes int16 PCM at the device's
  native rate/channels to `<seq>.wav` chunk files, updates a JSONL manifest, and
  records chunk rows in SQLite. Headers are rewritten when a chunk closes; a crash
  leaves a file whose header `repair_wav()` fixes from the file size.
- Real gaps (loopback silence produces no callbacks, pauses, device loss) start a
  new chunk at the true session offset, so the timeline is never compressed.
- Health: a mic that stops delivering callbacks is treated as stalled and reopened
  with bounded backoff. Loopback without callbacks while the stream is active is
  reported honestly as "idle (no playback)", not as healthy speech.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..config import paths
from ..db import write_session
from ..models import AudioChunk, AudioEvent, AudioSource, TranscriptionJob
from ..timebase import SessionClock
from .devices import PA_LOCK, ComScope, _pa_module, find_device, level_of, meter_value

log = logging.getLogger(__name__)

BACKOFF = [1, 2, 4, 8, 15, 30, 30, 30]
MIC_STALL_SECONDS = 3.0
GAP_THRESHOLD_S = 0.6


def _wav_header(rate: int, channels: int, data_bytes: int) -> bytes:
    byte_rate = rate * channels * 2
    return b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE" + b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, rate, byte_rate, channels * 2, 16
    ) + b"data" + struct.pack("<I", data_bytes)


def repair_wav(p: Path) -> tuple[int, int, int]:
    """Fix the header of a chunk interrupted mid-write. Returns (rate, channels, frames)."""
    with open(p, "r+b") as f:
        head = f.read(44)
        if len(head) < 44 or head[:4] != b"RIFF":
            raise ValueError("Not a chunk file")
        channels, rate = struct.unpack("<HI", head[22:28])
        size = os.path.getsize(p)
        data = max(0, size - 44)
        data -= data % (channels * 2)
        f.seek(0)
        f.write(_wav_header(rate, channels, data))
        f.truncate(44 + data)
    return rate, channels, data // (channels * 2)


class ChunkWriter:
    def __init__(self, path: Path, rate: int, channels: int):
        self.path = path
        self.rate = rate
        self.channels = channels
        self.frames = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "wb")
        self.f.write(_wav_header(rate, channels, 0))
        self._last_sync = time.monotonic()

    def write(self, data: bytes) -> None:
        self.f.write(data)
        self.frames += len(data) // (self.channels * 2)
        if time.monotonic() - self._last_sync > 2.0:
            self.f.flush()
            os.fsync(self.f.fileno())
            self._last_sync = time.monotonic()

    @property
    def duration_s(self) -> float:
        return self.frames / self.rate

    def close(self) -> None:
        data = self.frames * self.channels * 2
        self.f.seek(0)
        self.f.write(_wav_header(self.rate, self.channels, data))
        self.f.flush()
        os.fsync(self.f.fileno())
        self.f.close()


class SourceRecorder:
    def __init__(
        self,
        session_id: str,
        source_id: str,
        kind: str,
        device_name: Optional[str],
        label: str,
        clock: SessionClock,
        chunk_seconds: int,
        next_seq: int,
        on_chunk_closed: Callable[[str], None],
    ):
        self.session_id = session_id
        self.source_id = source_id
        self.kind = kind
        self.device_name = device_name
        self.label = label
        self.clock = clock
        self.chunk_seconds = max(5, chunk_seconds)
        self.seq = next_seq
        self.on_chunk_closed = on_chunk_closed
        self.dir = paths().audio / session_id / f"{kind}-{source_id[:8]}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.dir / "manifest.jsonl"

        self._q: "queue.Queue[tuple[bytes, float, int]]" = queue.Queue(maxsize=2000)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._retry_now = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self.state = "starting"  # starting|recording|idle|paused|reconnecting|failed|stopped
        self.error: Optional[str] = None
        self.level = 0.0
        self.last_callback = 0.0
        self.last_signal = 0.0
        self.interrupted_at_ms: Optional[int] = None
        self.attempt = 0
        self.dropped_buffers = 0
        self.rate = 0
        self.channels = 0
        self.resolved_device = device_name or ""
        self.heartbeat = time.monotonic()

    # ---------------------------------------------------------------- control
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"rec-{self.kind}")
        self._thread.start()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()
        self._retry_now.set()

    def retry(self) -> None:
        self.attempt = 0
        self._retry_now.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._retry_now.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        now = time.monotonic()
        receiving = self.last_callback > 0 and now - self.last_callback < 1.5
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "label": self.label,
            "device": self.resolved_device,
            "state": self.state,
            "receiving": receiving,
            "level": self.level if receiving else 0.0,
            "signal_recent": self.last_signal > 0 and now - self.last_signal < 5,
            "error": self.error,
            "interrupted_at_ms": self.interrupted_at_ms,
            "attempt": self.attempt,
            "dropped_buffers": self.dropped_buffers,
            "writer_alive": self.alive,
        }

    # ---------------------------------------------------------------- internals
    def _event(self, kind: str, message: str = "") -> None:
        try:
            with write_session() as s:
                s.add(AudioEvent(source_id=self.source_id, kind=kind, offset_ms=self.clock.offset_ms(), message=message))
                src = s.get(AudioSource, self.source_id)
                if src:
                    src.state = self.state
                    src.last_error = self.error
        except Exception:  # noqa: BLE001
            log.exception("Failed to record audio event")

    def _manifest(self, rec: dict) -> None:
        with open(self.manifest, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _open_stream(self):  # noqa: ANN202
        pyaudio = _pa_module()
        with PA_LOCK:
            p = pyaudio.PyAudio()
            try:
                d = find_device(p, self.kind, self.device_name)
                if d is None:
                    raise RuntimeError(f"Device not found: {self.device_name or 'system default'}")
                self.channels = int(d["maxInputChannels"])
                self.rate = int(d["defaultSampleRate"])
                self.resolved_device = d["name"]
                q = self._q

                def cb(in_data, frame_count, time_info, status):  # noqa: ANN001
                    try:
                        q.put_nowait((in_data, time.monotonic(), frame_count))
                    except queue.Full:
                        self.dropped_buffers += 1
                    return (None, pyaudio.paContinue)

                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=self.rate,
                    input=True,
                    input_device_index=int(d["index"]),
                    frames_per_buffer=int(self.rate * 0.1),
                    stream_callback=cb,
                )
                return p, stream
            except Exception:
                p.terminate()
                raise

    def _close_stream(self, p, stream) -> None:  # noqa: ANN001
        with PA_LOCK:
            try:
                if stream is not None:
                    try:
                        stream.stop_stream()
                    except Exception:  # noqa: BLE001
                        pass
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                if p is not None:
                    p.terminate()

    def _begin_chunk(self, start_mono: float, contiguous: bool) -> tuple[ChunkWriter, str, int]:
        self.seq += 1
        rel = f"{self.session_id}/{self.dir.name}/{self.seq:05d}.wav"
        path = paths().audio / rel
        start_ms = self.clock.offset_ms(start_mono)
        writer = ChunkWriter(path, self.rate, self.channels)
        with write_session() as s:
            chunk = AudioChunk(
                session_id=self.session_id, source_id=self.source_id, seq=self.seq, rel_path=rel,
                start_offset_ms=start_ms, sample_rate=self.rate, channels=self.channels,
                state="recording", contiguous_with_prev=contiguous,
            )
            s.add(chunk)
            s.flush()
            chunk_id = chunk.id
        self._manifest({"type": "open", "seq": self.seq, "chunk_id": chunk_id, "rel": rel, "start_ms": start_ms,
                        "rate": self.rate, "channels": self.channels})
        return writer, chunk_id, start_ms

    def _end_chunk(self, writer: ChunkWriter, chunk_id: str, peak: float) -> None:
        writer.close()
        duration_ms = int(writer.frames * 1000 / writer.rate)
        with write_session() as s:
            c = s.get(AudioChunk, chunk_id)
            if c is not None:
                c.duration_ms = duration_ms
                c.state = "complete"
                c.peak = peak
                if writer.frames > 0:
                    s.add(TranscriptionJob(session_id=self.session_id, chunk_id=chunk_id))
                else:
                    c.state = "deleted"
        if writer.frames == 0:
            try:
                writer.path.unlink()
            except OSError:
                pass
        self._manifest({"type": "close", "seq": self.seq, "chunk_id": chunk_id, "frames": writer.frames})
        try:
            self.on_chunk_closed(chunk_id)
        except Exception:  # noqa: BLE001
            log.exception("chunk callback failed")

    def _run(self) -> None:
        try:
            with ComScope():
                self._run_inner()
        except Exception as e:  # noqa: BLE001 - supervisor reports it
            log.exception("Recorder crashed")
            self.state = "failed"
            self.error = f"Recorder crashed: {e}"
            self._event("error", self.error)

    def _run_inner(self) -> None:
        was_interrupted = False
        while not self._stop.is_set():
            self.heartbeat = time.monotonic()
            if self._paused.is_set():
                self.state = "paused"
                self._retry_now.wait(0.5)
                self._retry_now.clear()
                continue
            p = stream = None
            try:
                self.state = "starting" if not was_interrupted else "reconnecting"
                p, stream = self._open_stream()
                if was_interrupted:
                    self._event("resumed", f"Recording resumed on {self.resolved_device}")
                    was_interrupted = False
                    self.interrupted_at_ms = None
                else:
                    self._event("started", self.resolved_device)
                self.error = None
                self.attempt = 0
                self.state = "recording" if self.kind == "mic" else "idle"
                self._record_loop(stream)
                if self._paused.is_set() and not self._stop.is_set():
                    continue
            except Exception as e:  # noqa: BLE001
                msg = str(e) or e.__class__.__name__
                if self._stop.is_set():
                    break
                self.error = msg
                if self.interrupted_at_ms is None:
                    self.interrupted_at_ms = self.clock.offset_ms()
                    self._event("interrupted", msg)
                was_interrupted = True
                if self.attempt >= len(BACKOFF):
                    self.state = "failed"
                    self._event("error", f"Gave up after {self.attempt} attempts: {msg}. Use Retry to try again.")
                    self._retry_now.wait()
                    self._retry_now.clear()
                    self.attempt = 0
                    continue
                self.state = "reconnecting"
                delay = BACKOFF[self.attempt]
                self.attempt += 1
                self._retry_now.wait(delay)
                self._retry_now.clear()
            finally:
                if stream is not None or p is not None:
                    self._close_stream(p, stream)
        self.state = "stopped"
        self._event("stopped")

    def _record_loop(self, stream) -> None:  # noqa: ANN001
        writer: Optional[ChunkWriter] = None
        chunk_id = ""
        chunk_peak = 0.0
        expected_next: Optional[float] = None
        try:
            while not self._stop.is_set() and not self._paused.is_set():
                self.heartbeat = time.monotonic()
                try:
                    data, arrival, frames = self._q.get(timeout=0.25)
                except queue.Empty:
                    now = time.monotonic()
                    active = False
                    try:
                        active = stream.is_active()
                    except Exception:  # noqa: BLE001
                        active = False
                    if not active:
                        raise RuntimeError("Audio stream stopped (device disconnected or in use)")
                    if self.kind == "mic":
                        ref = self.last_callback or (now - 0.1)
                        if now - ref > MIC_STALL_SECONDS:
                            self.last_callback = 0
                            raise RuntimeError("Microphone stopped delivering audio")
                    else:
                        if self.last_callback and now - self.last_callback > 1.0:
                            self.state = "idle"
                            self.level = 0.0
                    continue
                self.last_callback = arrival
                start_mono = arrival - frames / self.rate
                rms, peak = level_of(data, self.channels)
                self.level = meter_value(rms)
                if peak > 0.01:
                    self.last_signal = arrival
                self.state = "recording"
                gap = expected_next is not None and (start_mono - expected_next) > GAP_THRESHOLD_S
                if writer is not None and (gap or writer.duration_s >= self.chunk_seconds):
                    self._end_chunk(writer, chunk_id, chunk_peak)
                    writer = None
                if writer is None:
                    contiguous = expected_next is not None and not gap
                    # Each chunk is re-anchored to the clock so audio/monotonic drift never accumulates.
                    writer, chunk_id, _ = self._begin_chunk(start_mono, contiguous)
                    chunk_peak = 0.0
                writer.write(data)
                chunk_peak = max(chunk_peak, peak)
                if expected_next is None or gap or abs(start_mono - expected_next) > 0.3:
                    expected_next = start_mono
                expected_next += frames / self.rate
        finally:
            # Drain whatever is queued so nothing captured is lost.
            if writer is not None:
                while True:
                    try:
                        data, _a, _f = self._q.get_nowait()
                    except queue.Empty:
                        break
                    writer.write(data)
                self._end_chunk(writer, chunk_id, chunk_peak)
            else:
                while not self._q.empty():
                    try:
                        self._q.get_nowait()
                    except queue.Empty:
                        break
