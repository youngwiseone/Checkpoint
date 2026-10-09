import wave
from types import SimpleNamespace

import numpy as np
import pytest

from checkpoint.transcription.engine import WhistleEngine
from checkpoint.transcription.worker import load_chunk_audio
from checkpoint.settings_store import AppSettings, _upgrade


def test_upgrade_switches_model_and_keeps_preferences():
    settings = AppSettings.model_validate(_upgrade({"version": 3, "transcription": {
        "model": "small.en", "device": "cuda", "paused": True, "default_mode": "after"}}))
    assert settings.transcription.model == "whistle"
    assert settings.transcription.device == "cpu"
    assert settings.transcription.paused
    assert settings.transcription.default_mode == "after"


def test_long_audio_and_word_offsets():
    calls = []
    class Model:
        def transcribe(self, clip, **kwargs):
            calls.append((len(clip), kwargs))
            return {"text": "Hello", "words": [{"word": "Hello", "start": .2, "end": .6, "probability": .9}]}
    engine = WhistleEngine()
    engine._model = Model()
    rows = engine.transcribe(np.zeros(31 * 16000, dtype=np.float32), initial_prompt="Checkpoint, Siobhan")
    assert [n for n, _ in calls] == [30 * 16000, 16000]
    assert calls[0][1]["keywords"] == ["Checkpoint", "Siobhan"]
    assert calls[0][1]["word_timestamps"]
    assert rows[1]["start"] == pytest.approx(30.2)
    assert rows[1]["end"] == pytest.approx(30.6)
    assert rows[0]["confidence"] == .9


def test_silence_produces_no_rows():
    engine = WhistleEngine()
    engine._model = SimpleNamespace(transcribe=lambda *a, **k: {"text": "", "words": []})
    assert engine.transcribe(np.zeros(16000, dtype=np.float32)) == []


def test_native_recording_resampled_to_mono(app_env):
    from checkpoint.config import paths
    file = paths().audio / "stereo.wav"
    file.parent.mkdir(parents=True, exist_ok=True)
    samples = np.full((48000, 2), 8192, dtype=np.int16)
    with wave.open(str(file), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(samples.tobytes())
    audio = load_chunk_audio(SimpleNamespace(rel_path="stereo.wav"))
    assert audio.shape == (16000,)
    assert audio.dtype == np.float32
    assert np.mean(audio) == pytest.approx(.25, abs=.001)
