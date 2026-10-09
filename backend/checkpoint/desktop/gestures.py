"""Capture button gestures, kept separate from Windows input and Qt."""


def resolve_press(pressed, wait, now, stopped, hold_seconds=.55):
    started = now()
    while pressed() and not stopped():
        if now() - started >= hold_seconds:
            return "review"
        wait(.01)
    return None if stopped() else "capture"


class CaptureButton:
    def __init__(self):
        self.previous = None
        self.previous_at = 0

    def tap(self, grab, note, now):
        if self.previous and now() - self.previous_at <= .45:
            payload, self.previous = self.previous, None
            note({**payload, "needs_note": True})
        else:
            self.previous = grab()
            self.previous_at = now()

    def clear(self):
        self.previous = None
