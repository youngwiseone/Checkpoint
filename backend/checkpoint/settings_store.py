"""User preferences persisted in SQLite (secrets are NOT stored here — see sharing/secrets.py)."""

from __future__ import annotations

import threading
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .db import read_session, write_session
from .models import Setting


class HotkeySettings(BaseModel):
    capture: str = "F8"
    capture_context: str = "Shift+F8"
    quick_note: str = "F9"


class CaptureSettings(BaseModel):
    always_ask_context: bool = False
    # foreground_monitor: monitor containing the foreground window (default)
    # foreground_window: just the visible foreground window rectangle
    region: Literal["foreground_monitor", "foreground_window"] = "foreground_monitor"
    debounce_ms: int = 600


class AudioPrefs(BaseModel):
    # Only written when the user explicitly clicks "Remember these choices".
    mic_enabled: bool = False
    mic_device: Optional[str] = None
    mic_label: str = "Me"
    loopback_enabled: bool = False
    loopback_device: Optional[str] = None
    loopback_label: str = "Computer audio"
    chunk_seconds: int = 20


class TranscriptionSettings(BaseModel):
    model: str = "base.en"
    device: Literal["cpu", "cuda"] = "cpu"
    compute_type: str = "int8"
    default_mode: Literal["after", "live", "off"] = "after"
    paused: bool = False
    cpu_threads: int = 2
    overlap_seconds: float = 1.5


class AISettings(BaseModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:4b"
    timeout_seconds: int = 240
    max_retries: int = 2
    chunk_chars: int = 6000
    chunk_overlap_items: int = 4
    window_before_s: int = 30
    window_after_s: int = 20


class SharingSettings(BaseModel):
    server_url: str = ""
    display_name: str = ""
    allow_insecure_private_network: bool = False


class AppSettings(BaseModel):
    first_run_complete: bool = False
    hotkeys: HotkeySettings = Field(default_factory=HotkeySettings)
    capture: CaptureSettings = Field(default_factory=CaptureSettings)
    audio: AudioPrefs = Field(default_factory=AudioPrefs)
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    ai: AISettings = Field(default_factory=AISettings)
    sharing: SharingSettings = Field(default_factory=SharingSettings)
    last_project_id: Optional[str] = None


_KEY = "app"
_lock = threading.Lock()
_cache: AppSettings | None = None


def get_settings() -> AppSettings:
    global _cache
    with _lock:
        if _cache is None:
            with read_session() as s:
                row = s.get(Setting, _KEY)
                _cache = AppSettings.model_validate(row.value) if row else AppSettings()
        return _cache.model_copy(deep=True)


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def update_settings(patch: dict[str, Any]) -> AppSettings:
    global _cache
    with _lock:
        current = (_cache or AppSettings()).model_dump() if _cache else None
        if current is None:
            with read_session() as s:
                row = s.get(Setting, _KEY)
                current = AppSettings.model_validate(row.value).model_dump() if row else AppSettings().model_dump()
        merged = AppSettings.model_validate(_deep_merge(current, patch))
        with write_session() as s:
            row = s.get(Setting, _KEY)
            if row:
                row.value = merged.model_dump()
            else:
                s.add(Setting(key=_KEY, value=merged.model_dump()))
        _cache = merged
        return merged.model_copy(deep=True)


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None
