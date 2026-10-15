from checkpoint.desktop.shortcuts_linux import ShortcutListener


def listener():
    return ShortcutListener(lambda: {"capture": "F9"}, lambda action: None)


def test_triggers_replay_the_capture_key_gestures():
    shortcuts = listener()
    assert shortcuts._events("capture") == ["press", "capture"]
    assert shortcuts._events("review") == ["review"]
    assert shortcuts._events("voice") == ["press", "capture", "press", "hold"]
    assert shortcuts._events("voice") == ["release"]


def test_any_trigger_ends_a_running_voice_note_first():
    shortcuts = listener()
    shortcuts._events("voice")
    assert shortcuts._events("capture") == ["release", "press", "capture"]
    assert shortcuts._events("capture") == ["press", "capture"]


def test_summary_lists_a_command_for_every_gesture():
    bindings = listener().summary()["bindings"]
    assert [b["action"] for b in bindings] == ["capture", "review", "voice"]
    assert all(b["command"].endswith("trigger.py " + b["action"]) for b in bindings)
