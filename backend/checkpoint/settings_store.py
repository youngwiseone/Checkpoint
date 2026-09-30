"""User preferences persisted in SQLite (secrets are NOT stored here — see sharing/secrets.py)."""

from __future__ import annotations

import threading
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .db import read_session, write_session
from .models import Setting

SETTINGS_VERSION = 2


class HotkeySettings(BaseModel):
    capture: str = "F8"
    capture_context: str = "Shift+F8"
    quick_note: str = "F9"
    start_session: str = "Ctrl+F8"  # start a session (the offered project, else the last one); press twice quickly to end
    review: str = "Ctrl+F9"  # the keyboard-first review overlay


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


class AutoCaptureSettings(BaseModel):
    # A System One (Jev-style) decision model reads the live transcript and decides when a screenshot is worth taking.
    enabled: bool = False
    model: str = "tev1:0.8b"
    # On real playtest transcripts tev1:0.8b scores ordinary chatter around 0.5 and clear problem reports 0.7+.
    threshold: float = 0.7  # capture when the model's probability is at least this
    frame_interval_s: float = 2.0
    buffer_seconds: int = 90
    cooldown_s: int = 45
    max_per_session: int = 25
    lead_s: float = 1.5  # people describe what they've just seen: take the frame this long before the remark


class WatchRule(BaseModel):
    project_id: str
    # exe: a running program's exe name (with or without .exe); title: part of a visible window title.
    kind: Literal["exe", "title"] = "exe"
    match: str = Field(min_length=1, max_length=200)


class AppWatchSettings(BaseModel):
    # When a project's program appears, offer to start a session for it (tray + toast, start with the hotkey).
    enabled: bool = True
    rules: list[WatchRule] = Field(default_factory=list)


class AgentSettings(BaseModel):
    # Leave empty to find the agent on PATH or in its usual install folders.
    claude_path: str = ""
    codex_path: str = ""


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
    auto_capture: AutoCaptureSettings = Field(default_factory=AutoCaptureSettings)
    app_watch: AppWatchSettings = Field(default_factory=AppWatchSettings)
    sharing: SharingSettings = Field(default_factory=SharingSettings)
    agents: AgentSettings = Field(default_factory=AgentSettings)
    last_project_id: Optional[str] = None
    version: int = SETTINGS_VERSION


def _upgrade(value: dict) -> dict:
    """Stored settings keep every value, so improved defaults only reach users still on the old ones."""
    if value.get("version", 1) < 2:
        ac = value.setdefault("auto_capture", {})
        for k, old, new in (("threshold", 0.5, 0.7), ("cooldown_s", 15, 45), ("max_per_session", 40, 25)):
            if ac.get(k, old) == old:
                ac[k] = new
    value["version"] = SETTINGS_VERSION
    return value


def _load(row) -> AppSettings:  # noqa: ANN001
    return AppSettings.model_validate(_upgrade(dict(row.value))) if row else AppSettings()


_KEY = "app"
_lock = threading.Lock()
_cache: AppSettings | None = None


def get_settings() -> AppSettings:
    global _cache
    with _lock:
        if _cache is None:
            with read_session() as s:
                row = s.get(Setting, _KEY)
                _cache = _load(row)
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
                current = _load(row).model_dump()
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
