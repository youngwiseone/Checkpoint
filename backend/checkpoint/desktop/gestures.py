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
    """Tap captures, double tap edits, hold reviews, and tap-then-hold dictates."""
    def __init__(self, schedule=_schedule):
        from threading import RLock
        self.previous = None
        self.pending = None
        self.pending_at = 0
        self._timer = None
        self._schedule = schedule
        self._lock = RLock()
        self._continuation = False

    def press(self, now):
        """Reserve a pending tap while the next press is being resolved."""
        with self._lock:
            if self.pending is None:
                return False
            if now() - self.pending_at > .45:
                self._finish(self.pending)
                return False
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._continuation = True
            return True

    def tap(self, grab, note, now, latest=lambda: None, voice=lambda: None):
        with self._lock:
            if self.pending is not None and not self._continuation and now() - self.pending_at > .45:
                self._finish(self.pending)
            if self.pending is not None:
                self._continuation = False
                self._finish(self.pending, edit=True)
                return
            self.pending = object()
            self._pending_grab, self._note, self._latest, self._voice = grab, note, latest, voice
            self.pending_at = now()
            token = self.pending
            self._timer = self._schedule(.45, lambda: self._finish(token))

    def hold(self, review):
        with self._lock:
            callback = self._voice if self.pending is not None and self._continuation else review
            self._cancel_pending()
        callback()

    def _finish(self, token, edit=False):
        with self._lock:
            if self.pending is not token or self._continuation:
                return
            grab, note, latest = self._pending_grab, self._note, self._latest
            self._cancel_pending()
            if not edit:
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
        self._continuation = False

    def clear(self):
        with self._lock:
            self._cancel_pending()
