"""Whistle local transcription with explicit downloads and word timestamps."""

from __future__ import annotations

import logging
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
    "whistle": {"repo": "Cactus-Compute/whistle", "approx_mb": 17, "note": "Whistle · local CPU · seven languages"},
}
REQUIRED_FILES = ("whistle.cact",)


def runtime_path() -> Path:
    from needle.agent import fetch
    return model_dir("whistle") / fetch.lib_name(3)


class ModelMissing(Exception):
    pass


def model_dir(name: str) -> Path:
    return paths().models / "whistle" / name


def model_installed(name: str) -> bool:
    d = model_dir(name)
    return name in MODEL_CHOICES and all((d / f).is_file() for f in REQUIRED_FILES) and runtime_path().is_file()


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
            patterns = list(REQUIRED_FILES)
            try:
                info = snapshot_download(repo, allow_patterns=patterns, dry_run=True)
                total = sum(getattr(f, "file_size", 0) or 0 for f in info)  # type: ignore[union-attr]
                if total:
                    with self._lock:
                        self.state[name]["bytes_total"] = total
            except Exception:  # noqa: BLE001 - size is only for the progress bar
                pass
            snapshot_download(repo, allow_patterns=patterns, local_dir=str(model_dir(name)))
            from needle.agent import fetch
            fetch.fetch_library(dest_dir=str(model_dir(name)), generation=3)
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


# Whistle's native runtime holds one model per process and is not thread-safe.
_RUNTIME_LOCK = threading.RLock()
os.environ["NEEDLE_TELEMETRY"] = "0"


class WhistleEngine:
    def __init__(self) -> None:
        self._model = None
        self.device = "cpu"
        self.fallback_message = None

    def ensure(self, name: str, device: str, compute_type: str, cpu_threads: int) -> None:
        if not model_installed(name):
            raise ModelMissing("Whistle is not installed. Download it in Settings → Transcript.")
        with _RUNTIME_LOCK:
            if self._model is None:
                os.environ["NEEDLE3_LIB_PATH"] = str(runtime_path())
                from needle import Whistle
                self._model = Whistle(weights=str(model_dir(name) / "whistle.cact"))

    def unload(self) -> None:
        with _RUNTIME_LOCK:
            self._model = None

    def transcribe(self, audio, *, english_only: bool = False, initial_prompt: Optional[str] = None) -> list[dict]:
        # Persisted recordings can exceed the engine's 30-second input limit.
        keywords = [t.strip() for t in (initial_prompt or "").split(",") if t.strip()]
        out = []
        with _RUNTIME_LOCK:
            if self._model is None:
                raise RuntimeError("Whistle has not been loaded")
            for offset in range(0, len(audio), 30 * 16000):
                clip = audio[offset:offset + 30 * 16000]
                result = self._model.transcribe(clip, language="en" if english_only else None,
                                               keywords=keywords or None, word_timestamps=True)
                if not result.get("text", "").strip():
                    continue
                words = result.get("words") or []
                if not words:
                    raise RuntimeError("Whistle returned speech without word timestamps")
                # Word-level rows let the worker drop only words from the previous chunk's overlap.
                for word in words:
                    text = word["word"].strip()
                    if not text:
                        continue
                    confidence = max(0.0, min(1.0, float(word["probability"])))
                    start = max(0.0, min(len(clip) / 16000, float(word["start"])))
                    end = max(start, min(len(clip) / 16000, float(word["end"])))
                    out.append({"start": offset / 16000 + start, "end": offset / 16000 + end,
                                "text": text, "confidence": confidence, "low": confidence < 0.5})
        return out
