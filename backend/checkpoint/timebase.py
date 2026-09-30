"""One timebase per session.

Offsets are real elapsed milliseconds since the session's UTC start. Pauses and
process restarts are *not* compressed out, so a screenshot at offset T lines up
with audio recorded at offset T. Within a running process we use the monotonic
clock (immune to wall-clock adjustments) anchored to UTC at process start; after
a restart the anchor is re-established from UTC, which is persisted.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


class SessionClock:
    def __init__(self, started_at: datetime):
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        self.started_at = started_at
        self._anchor_mono = time.monotonic()
        self._anchor_utc = datetime.now(timezone.utc)

    def offset_ms(self, mono: float | None = None) -> int:
        mono = time.monotonic() if mono is None else mono
        base = (self._anchor_utc - self.started_at).total_seconds()
        return int(round((base + (mono - self._anchor_mono)) * 1000))

    def utc_at(self, mono: float | None = None) -> datetime:
        return self.started_at + timedelta(milliseconds=self.offset_ms(mono))

    @staticmethod
    def offset_for_utc(started_at: datetime, when: datetime) -> int:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return int(round((when - started_at).total_seconds() * 1000))


def fmt_offset(ms: int | None) -> str:
    if ms is None:
        return "--:--"
    neg = ms < 0
    s = abs(ms) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    out = f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
    return f"-{out}" if neg else out
