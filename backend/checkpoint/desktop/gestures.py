"""Capture button gestures, independent of Windows input and Qt."""


def resolve_press(pressed, wait, now, stopped, hold_seconds=.55, started=None):
    started = now() if started is None else started
    while pressed() and not stopped():
        if now() - started >= hold_seconds:
            return 'hold'
        wait(.01)
    return None if stopped() else 'capture'


def _schedule(seconds, callback):
    from threading import Timer
    timer = Timer(seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


class CaptureButton:
    """Delay single/double actions until a third tap has been ruled out."""
    def __init__(self, schedule=_schedule):
        from threading import RLock
        self.previous = None
        self.pending = None
        self.pending_at = 0
        self._timer = None
        self._schedule = schedule
        self._lock = RLock()
        self._count = 0

    def tap(self, grab, note, now, review=lambda: None, latest=lambda: None):
        with self._lock:
            if self.pending is not None and now() - self.pending_at > .45:
                self._finish(self.pending)
            if self.pending is None:
                self.pending = object()
                self._count = 0
                self._pending_grab, self._note, self._review, self._latest = grab, note, review, latest
            self._count += 1
            self.pending_at = now()
            if self._timer:
                self._timer.cancel()
            if self._count == 3:
                callback = self._review
                self._cancel_pending()
                callback()
                return
            token = self.pending
            self._timer = self._schedule(.45, lambda: self._finish(token))

    def _finish(self, token):
        with self._lock:
            if self.pending is not token:
                return
            count, grab, note, latest = self._count, self._pending_grab, self._note, self._latest
            self._cancel_pending()
            if count == 1:
                self.previous = grab()
            else:
                self.previous = latest() or self.previous
                if self.previous is None:
                    self.previous = grab()
                if self.previous:
                    note({**self.previous, 'needs_note': True})

    def _cancel_pending(self):
        if self._timer:
            self._timer.cancel()
        self.pending = self._timer = None
        self._count = 0

    def clear(self):
        with self._lock:
            self._cancel_pending()
