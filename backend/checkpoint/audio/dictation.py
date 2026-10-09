"""Short, explicit microphone dictation for the note editor."""
from __future__ import annotations

import threading

import numpy as np

from .devices import PA_LOCK, ComScope, _pa_module, find_device


def transcribe_pcm(data: bytes, channels: int, rate: int) -> str:
    import av
    from ..transcription.engine import WhistleEngine

    samples = np.frombuffer(data, dtype=np.int16).reshape(-1, channels).astype(np.float32).mean(axis=1) / 32768
    if not len(samples):
        return ""
    frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
    frame.sample_rate = rate
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
    frames = resampler.resample(frame) + resampler.resample(None)
    audio = np.clip(np.concatenate([f.to_ndarray().reshape(-1) for f in frames]), -1, 1)
    engine = WhistleEngine()
    engine.ensure("whistle", "cpu", "int8", 2)
    return " ".join(s["text"] for s in engine.transcribe(audio)).strip()


class NoteDictation:
    """Owns a temporary recording. No audio is written to disk."""
    def __init__(self, device=None):
        self.device = device
        self.stop_event = threading.Event()
        self.cancelled = threading.Event()
        self.phase = "starting"
        self.text = ""
        self.error = ""
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="note-dictation")
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def cancel(self):
        self.cancelled.set()
        self.stop()

    def _run(self):
        p = stream = None
        blocks = []
        try:
            # COM must remain initialised until the WASAPI stream has closed.
            with ComScope():
                try:
                    pyaudio = _pa_module()
                    with PA_LOCK:
                        p = pyaudio.PyAudio()
                        device = find_device(p, "mic", self.device)
                        if device is None:
                            raise RuntimeError("Microphone not found. Choose one in Settings → Audio.")
                        channels, rate = int(device["maxInputChannels"]), int(device["defaultSampleRate"])
                        count = 0
                        limit = rate * 60

                        def callback(data, frames, info, status):
                            nonlocal count
                            remaining = max(0, limit - count)
                            blocks.append(data[:remaining * channels * 2])
                            count += min(frames, remaining)
                            done = self.stop_event.is_set() or count >= limit
                            if count >= limit:
                                self.stop_event.set()
                            return None, pyaudio.paComplete if done else pyaudio.paContinue

                        stream = p.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                                        input_device_index=int(device["index"]), frames_per_buffer=max(1, rate // 20),
                                        stream_callback=callback)
                    self.phase = "recording"
                    while not self.stop_event.wait(.05):
                        if not stream.is_active():
                            raise RuntimeError("Microphone stopped sending audio. Check its connection and try again.")
                finally:
                    with PA_LOCK:
                        try:
                            if stream is not None:
                                stream.stop_stream()
                                stream.close()
                        finally:
                            if p is not None:
                                p.terminate()
            if not self.cancelled.is_set():
                self.phase = "transcribing"
                self.text = transcribe_pcm(b"".join(blocks), channels, rate)
        except Exception as error:
            self.error = str(error)
        finally:
            self.phase = "done"
