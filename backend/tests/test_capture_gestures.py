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


def test_hold_starts_dictation_and_tap_waits_for_release():
    for duration, expected in ((.1, "capture"), (2, "hold")):
        now = [0.0]
        def wait(seconds):
            now[0] += seconds
        assert resolve_press(lambda: now[0] < duration, wait, lambda: now[0], lambda: False) == expected
        if expected == "hold":
            assert .55 <= now[0] < .6


def test_shutdown_during_hold_does_not_capture():
    assert resolve_press(lambda: True, lambda _: None, lambda: 0, lambda: True) is None


def test_existing_default_shortcuts_migrate_to_one_f9_key():
    from checkpoint.settings_store import _upgrade, AppSettings
    settings = AppSettings.model_validate(_upgrade({"version": 4, "hotkeys": {"capture": "F8", "review": "F9"}}))
    assert settings.hotkeys.capture == "F9"
    assert settings.hotkeys.review == ""


def test_triple_tap_opens_review_without_capture_or_editor():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    shots, notes, reviews = [], [], []
    for at in (1, 1.2, 1.4):
        button.tap(lambda: shots.append(True), notes.append, lambda at=at: at, lambda: reviews.append(True))
    scheduler.flush()
    assert shots == notes == [] and reviews == [True]


def test_double_tap_uses_persisted_latest_task_after_restart():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    notes = []
    def grab():
        raise AssertionError('Double tap should not capture when a task already exists')
    for at in (1, 1.2):
        button.tap(grab, notes.append, lambda at=at: at, latest=lambda: {'id': 'persisted', 'text': 'Previous note'})
    assert notes == []  # Give a third tap time to arrive.
    scheduler.flush()
    assert notes[0]['id'] == 'persisted' and notes[0]['text'] == 'Previous note'


def test_capture_hotkey_without_audio_keeps_multitap_gestures():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from checkpoint.desktop.host import DesktopHost
    capture = SimpleNamespace(hotkey_capture=Mock())
    host = DesktopHost.__new__(DesktopHost)
    host.note_window = SimpleNamespace(isVisible=lambda: False)
    host.overlay = SimpleNamespace(isVisible=lambda: False)
    host.app = SimpleNamespace(activeModalWidget=lambda: None)
    host.core = SimpleNamespace(capture=capture, sessions=SimpleNamespace(active=None))
    host.capture_button = SimpleNamespace(tap=Mock())
    host.bridge = SimpleNamespace(note_requested=SimpleNamespace(emit=Mock()))
    host._on_hotkey('capture')
    host.capture_button.tap.assert_called_once()
    capture.hotkey_capture.assert_not_called()
    # Only a confirmed single tap captures and opens the note window.
    host.capture_button.tap.call_args.args[0]()
    capture.hotkey_capture.assert_called_once_with(ask_context=True, reason='no_audio')
