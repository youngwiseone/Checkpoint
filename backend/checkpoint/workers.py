"""Supervised background workers.

Expensive work (transcription, extraction) shares HEAVY_LOCK so the two never
compete for CPU/GPU memory unless the user changes that behaviour.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .events import notices

log = logging.getLogger(__name__)

HEAVY_LOCK = threading.Lock()


class Worker:
    name = "worker"
    idle_sleep = 1.0

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.heartbeat = time.monotonic()
        self.crashes = 0
        self.last_error: Optional[str] = None
        self.busy = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=self.name)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def wake(self) -> None:
        self._wake.set()

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.heartbeat = time.monotonic()
            try:
                did_work = self.step()
            except Exception as e:  # noqa: BLE001
                self.crashes += 1
                self.last_error = str(e)
                log.exception("%s step failed", self.name)
                did_work = False
                self._wake.wait(min(30, 2 ** min(self.crashes, 5)))
                self._wake.clear()
                continue
            if not did_work:
                self._wake.wait(self.idle_sleep)
                self._wake.clear()

    def step(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError


class Supervisor:
    """Restarts dead worker threads and reports stalls."""

    def __init__(self, workers: list[Worker], extra_checks: Optional[list[Callable[[], None]]] = None):
        self.workers = workers
        self.extra_checks = extra_checks or []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="supervisor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(5):
            for w in self.workers:
                if not w.alive and not w.stopping:
                    notices.push("warning", f"{w.name} stopped unexpectedly and was restarted.", w.name)
                    w.start()
            for check in self.extra_checks:
                try:
                    check()
                except Exception:  # noqa: BLE001
                    log.exception("supervisor check failed")
