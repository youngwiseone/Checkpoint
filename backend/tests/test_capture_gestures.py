from checkpoint.desktop.gestures import CaptureButton, resolve_press


def test_tap_double_tap_reuses_first_capture():
    button = CaptureButton()
    now = [1.0]
    shots, notes = [], []
    def grab():
        payload = {"id": str(len(shots)), "thumb_path": "first screenshot"}
        shots.append(payload)
        return payload
    button.tap(grab, notes.append, lambda: now[0])
    now[0] += .2
    button.tap(grab, notes.append, lambda: now[0])
    assert len(shots) == len(notes) == 1
    assert notes[0]["id"] == shots[0]["id"]
    now[0] += 1
    button.tap(grab, notes.append, lambda: now[0])
    assert len(shots) == 2


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
