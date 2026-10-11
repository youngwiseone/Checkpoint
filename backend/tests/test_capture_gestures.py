from checkpoint.desktop.gestures import CaptureButton, resolve_press


class Scheduler:
    def __init__(self):
        self.jobs = []

    def schedule(self, seconds, callback):
        job = type('Job', (), {'cancelled': False})()
        job.cancel = lambda: setattr(job, 'cancelled', True)
        self.jobs.append((job, callback))
        return job

    def flush(self):
        jobs, self.jobs = self.jobs, []
        for job, callback in jobs:
            if not job.cancelled:
                callback()


def test_later_double_tap_edits_previous_without_another_capture():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    now = [1.0]
    shots, notes = [], []
    def grab():
        payload = {"id": str(len(shots)), "thumb_path": "screenshot"}
        shots.append(payload)
        return payload
    button.tap(grab, notes.append, lambda: now[0])
    assert not shots
    scheduler.flush()
    assert len(shots) == 1
    now[0] += 5
    button.tap(grab, notes.append, lambda: now[0])
    now[0] += .2
    button.tap(grab, notes.append, lambda: now[0])
    scheduler.flush()
    assert len(shots) == len(notes) == 1
    assert notes[0]['id'] == shots[0]['id']
    now[0] += 1
    button.tap(grab, notes.append, lambda: now[0])
    scheduler.flush()
    assert len(shots) == 2


def test_first_double_tap_captures_once_and_adds_text():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    shots, notes = [], []
    def grab():
        shots.append(True)
        return {'id': 'first'}
    button.tap(grab, notes.append, lambda: 1)
    button.tap(grab, notes.append, lambda: 1.2)
    scheduler.flush()
    assert len(shots) == len(notes) == 1


def test_hold_cancels_pending_capture_and_keeps_last_screenshot():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    button.previous = {'id': 'last'}
    shots = []
    button.tap(lambda: shots.append(True), lambda _: None, lambda: 1)
    button.clear()
    scheduler.flush()
    assert shots == []
    assert button.previous == {'id': 'last'}


def test_hold_opens_review_and_tap_waits_for_release():
    for duration, expected in ((.1, "capture"), (2, "review")):
        now = [0.0]
        def wait(seconds):
            now[0] += seconds
        assert resolve_press(lambda: now[0] < duration, wait, lambda: now[0], lambda: False) == expected
        if expected == "review":
            assert .55 <= now[0] < .6


def test_shutdown_during_hold_does_not_capture():
    assert resolve_press(lambda: True, lambda _: None, lambda: 0, lambda: True) is None


def test_existing_default_shortcuts_migrate_to_one_f9_key():
    from checkpoint.settings_store import _upgrade, AppSettings
    settings = AppSettings.model_validate(_upgrade({"version": 4, "hotkeys": {"capture": "F8", "review": "F9"}}))
    assert settings.hotkeys.capture == "F9"
    assert settings.hotkeys.review == ""
