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


def test_hold_threshold_and_tap_waits_for_release():
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


def test_plain_hold_opens_review_without_capture_or_voice_note():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    reviews = []
    assert not button.press(lambda: 1)
    button.hold(lambda: reviews.append(True))
    scheduler.flush()
    assert reviews == [True] and button.pending is None


def test_tap_then_hold_reserves_first_capture_and_dictates_without_opening_editor():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    shots, notes, reviews, voices = [], [], [], []
    button.tap(lambda: shots.append(True), notes.append, lambda: 1, voice=lambda: voices.append(True))
    timer_callback = scheduler.jobs[0][1]
    assert button.press(lambda: 1.2)
    scheduler.flush()  # The single-tap timeout passes during the second hold.
    timer_callback()  # Even a timer already waiting for the lock cannot capture.
    assert shots == notes == voices == reviews == []
    button.hold(lambda: reviews.append(True))
    scheduler.flush()
    assert voices == [True] and shots == notes == reviews == []
    assert button.pending is None


def test_second_press_released_before_hold_edits_even_after_tap_timeout():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    notes, voices = [], []
    button.tap(lambda: {'id': 'first'}, notes.append, lambda: 1, voice=lambda: voices.append(True))
    assert button.press(lambda: 1.4)
    scheduler.flush()
    button.tap(lambda: {'id': 'second'}, notes.append, lambda: 1.8)
    scheduler.flush()
    assert notes == [{'id': 'first', 'needs_note': True}] and voices == []


def test_hold_after_tap_window_is_a_separate_menu_gesture():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    shots, reviews, voices = [], [], []
    button.tap(lambda: shots.append(True), lambda _: None, lambda: 1, voice=lambda: voices.append(True))
    assert not button.press(lambda: 1.6)
    button.hold(lambda: reviews.append(True))
    scheduler.flush()
    assert shots == reviews == [True] and voices == []


def test_clear_during_second_press_cancels_pending_voice_note():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    shots, voices = [], []
    button.tap(lambda: shots.append(True), lambda _: None, lambda: 1, voice=lambda: voices.append(True))
    assert button.press(lambda: 1.2)
    button.clear()
    scheduler.flush()
    assert shots == voices == [] and button.pending is None


def test_double_tap_uses_persisted_latest_task_after_restart():
    scheduler = Scheduler()
    button = CaptureButton(scheduler.schedule)
    notes = []
    def grab():
        raise AssertionError('Double tap should not capture when a task already exists')
    for at in (1, 1.2):
        button.tap(grab, notes.append, lambda at=at: at, latest=lambda: {'id': 'persisted', 'text': 'Previous note'})
    assert notes[0]['id'] == 'persisted'  # Double tap can now open immediately on release.
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


def test_host_plain_hold_opens_menu_without_starting_microphone(app_env, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from checkpoint.desktop import host as desktop
    from checkpoint.audio import dictation
    from checkpoint.capture import screen
    from checkpoint.settings_store import update_settings
    update_settings({'audio': {'mic_enabled': True}})
    microphone = Mock()
    monkeypatch.setattr(dictation, 'NoteDictation', microphone)
    monkeypatch.setattr(screen, 'grab', Mock(return_value='frame'))
    host = desktop.DesktopHost.__new__(desktop.DesktopHost)
    host.note_window = SimpleNamespace(isVisible=lambda: False)
    host.overlay = SimpleNamespace(isVisible=lambda: False)
    host.app = SimpleNamespace(activeModalWidget=lambda: None)
    host.core = SimpleNamespace(capture=Mock(), sessions=SimpleNamespace(active=None))
    host.bridge = SimpleNamespace(note_requested=SimpleNamespace(emit=Mock()))
    host._overlay_hotkey = Mock()
    scheduler = Scheduler()
    host.capture_button = CaptureButton(scheduler.schedule)
    host._on_hotkey('press')
    host._on_hotkey('hold')
    host._on_hotkey('release')
    scheduler.flush()
    host._overlay_hotkey.assert_called_once_with('review')
    microphone.assert_not_called()
    host.core.capture.hotkey_capture.assert_not_called()
    host.bridge.note_requested.emit.assert_not_called()
