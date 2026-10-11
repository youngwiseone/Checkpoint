"""Capture button gestures, kept separate from Windows input and Qt."""


def resolve_press(pressed, wait, now, stopped, hold_seconds=.55):
    started = now()
    while pressed() and not stopped():
        if now() - started >= hold_seconds:
            return "review"
        wait(.01)
    return None if stopped() else "capture"


def _schedule(seconds, callback):
    from threading import Timer
    timer = Timer(seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


class CaptureButton:
    """Wait for a complete gesture before capturing; double-tap edits the last shot."""
    def __init__(self, schedule=_schedule):
        from threading import RLock
        self.previous = None
        self.pending = None
        self.pending_at = 0
        self._timer = None
        self._schedule = schedule
        self._lock = RLock()

    def tap(self, grab, note, now):
        with self._lock:
            if self.pending is not None and now() - self.pending_at <= .45:
                self._cancel_pending()
                if self.previous is None:
                    self.previous = grab()
                if self.previous:
                    note({**self.previous, "needs_note": True})
                return
            # A scheduler may be late: finish an expired single tap first.
            if self.pending is not None:
                self._finish(self.pending)
            token = object()
            self.pending = token
            self.pending_at = now()
            self._timer = self._schedule(.45, lambda: self._capture(token, grab))
            self._pending_grab = grab

    def _capture(self, token, grab):
        with self._lock:
            if self.pending is token:
                self._cancel_pending()
                self.previous = grab()

    def _finish(self, token):
        self._capture(token, self._pending_grab)

    def _cancel_pending(self):
        if self._timer:
            self._timer.cancel()
        self.pending = self._timer = None

    def clear(self):
        with self._lock:
            self._cancel_pending()
