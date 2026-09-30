"""faster-whisper wrapper: explicit model downloads, local-only loading, CPU fallback."""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

import os

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from ..config import paths
from ..storage import dir_size

log = logging.getLogger(__name__)

MODEL_CHOICES = {
    "tiny.en": {"repo": "Systran/faster-whisper-tiny.en", "approx_mb": 75, "note": "Fastest, least accurate"},
    "base.en": {"repo": "Systran/faster-whisper-base.en", "approx_mb": 145, "note": "Recommended starting point (CPU friendly)"},
    "small.en": {"repo": "Systran/faster-whisper-small.en", "approx_mb": 485, "note": "Better accuracy, slower on CPU"},
    "medium.en": {"repo": "Systran/faster-whisper-medium.en", "approx_mb": 1530, "note": "High accuracy; GPU recommended"},
    "distil-large-v3": {"repo": "Systran/faster-distil-whisper-large-v3", "approx_mb": 1510, "note": "Large-model quality, GPU recommended"},
}
REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")


class ModelMissing(Exception):
    pass


def model_dir(name: str) -> Path:
    return paths().models / "whisper" / name


def model_installed(name: str) -> bool:
    d = model_dir(name)
    return all((d / f).is_file() for f in REQUIRED_FILES)


class DownloadManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: dict[str, dict] = {}

    def status(self) -> dict:
        with self._lock:
            out = {k: dict(v) for k, v in self.state.items()}
        for name, st in out.items():
            if st.get("state") == "downloading":
                st["bytes_done"] = dir_size(model_dir(name))
        return out

    def start(self, name: str) -> dict:
        if name not in MODEL_CHOICES:
            raise ValueError(f"Unknown model {name}")
        with self._lock:
            cur = self.state.get(name)
            if cur and cur.get("state") == "downloading":
                return cur
            self.state[name] = {"state": "downloading", "bytes_done": 0, "bytes_total": MODEL_CHOICES[name]["approx_mb"] * 1_000_000,
                                "error": None, "started": time.time()}
        threading.Thread(target=self._run, args=(name,), daemon=True, name=f"download-{name}").start()
        return self.state[name]

    def _run(self, name: str) -> None:
        try:
            from huggingface_hub import snapshot_download

            repo = MODEL_CHOICES[name]["repo"]
            patterns = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
            try:
                info = snapshot_download(repo, allow_patterns=patterns, dry_run=True)
                total = sum(getattr(f, "file_size", 0) or 0 for f in info)  # type: ignore[union-attr]
                if total:
                    with self._lock:
                        self.state[name]["bytes_total"] = total
            except Exception:  # noqa: BLE001 - size is only for the progress bar
                pass
            snapshot_download(repo, allow_patterns=patterns, local_dir=str(model_dir(name)))
            if not model_installed(name):
                raise RuntimeError("Download finished but model files are incomplete")
            with self._lock:
                self.state[name].update(state="done", bytes_done=dir_size(model_dir(name)))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "ConnectError" in repr(e) or "getaddrinfo" in msg or "Connection" in msg:
                msg = f"Couldn't reach Hugging Face to download the model. Check your internet connection. ({msg[:200]})"
            with self._lock:
                self.state[name].update(state="failed", error=msg)
            log.exception("Model download failed")


downloads = DownloadManager()


HALLUCINATIONS = re.compile(
    r"^\W*(thank(s| you)( so much)?( for watching| for listening)?|please subscribe|subscribe to (my|the) channel|"
    r"you|bye\.?|\.+|music|\[music\]|\(music\)|♪+)\W*$",
    re.IGNORECASE,
)


class WhisperEngine:
    def __init__(self) -> None:
        self._model = None
        self._key: Optional[tuple] = None
        self.fallback_message: Optional[str] = None

    def ensure(self, name: str, device: str, compute_type: str, cpu_threads: int) -> None:
        key = (name, device, compute_type, cpu_threads)
        if self._model is not None and self._key == key:
            return
        if not model_installed(name):
            raise ModelMissing(f"Transcription model '{name}' is not installed. Download it in Settings → Transcription.")
        from faster_whisper import WhisperModel

        self._model = None
        path = str(model_dir(name))
        if device == "cuda":
            try:
                self._model = WhisperModel(path, device="cuda", compute_type="float16" if compute_type == "int8" else compute_type,
                                           local_files_only=True)
                self.fallback_message = None
            except Exception as e:  # noqa: BLE001
                self.fallback_message = f"GPU transcription unavailable ({str(e)[:160]}). Using CPU instead."
                log.warning(self.fallback_message)
        if self._model is None:
            self._model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=cpu_threads, local_files_only=True)
        self._key = key

    def unload(self) -> None:
        self._model = None
        self._key = None

    def transcribe(self, audio, *, english_only: bool, initial_prompt: Optional[str]) -> list[dict]:  # noqa: ANN001
        assert self._model is not None
        segments, _info = self._model.transcribe(
            audio,
            language="en" if english_only else None,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt=initial_prompt or None,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        out: list[dict] = []
        prev_text = None
        repeats = 0
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            if HALLUCINATIONS.match(text) and (seg.no_speech_prob > 0.3 or seg.avg_logprob < -0.7):
                continue
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8:
                continue
            if text == prev_text:
                repeats += 1
                if repeats >= 1:  # identical consecutive lines are a classic Whisper loop
                    continue
            else:
                repeats = 0
            prev_text = text
            low = seg.avg_logprob < -1.0 or seg.compression_ratio > 2.4 or seg.no_speech_prob > 0.5
            conf = float(max(0.0, min(1.0, 1.0 + seg.avg_logprob / 2)))
            out.append({"start": float(seg.start), "end": float(seg.end), "text": text, "confidence": conf, "low": low})
        return out
