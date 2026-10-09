from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from test_native_desktop import app


def window(monkeypatch):
    from checkpoint.desktop.windows import NoteWindow
    from checkpoint.audio import dictation
    from checkpoint.transcription import engine
    fake = SimpleNamespace(phase="recording", text="", error="", start=Mock(), stop=Mock(), cancel=Mock())
    monkeypatch.setattr(dictation, "NoteDictation", lambda device: fake)
    monkeypatch.setattr(engine, "model_installed", lambda name: True)
    saved = Mock(return_value=None)
    win = NoteWindow(saved, Mock(), Mock(return_value=None))
    win.payload = {"id": "screenshot"}
    return win, fake, saved


def test_dictation_appends_and_saves_same_capture(app_env, monkeypatch):
    application = app()
    win, recording, saved = window(monkeypatch)
    win.text.setPlainText("Typed context.")
    win._toggle_dictation()
    win.text.insertPlainText(" More typing.")
    before = win.text.toPlainText()
    win._toggle_dictation()
    recording.stop.assert_called_once()
    recording.text = "The button is broken."
    recording.phase = "done"
    win._poll_dictation()
    assert win.text.toPlainText() == before + " The button is broken."
    saved.assert_not_called()
    win._save()
    saved.assert_called_once_with({"id": "screenshot"}, before + " The button is broken.", None)


def test_save_waits_for_transcription(app_env, monkeypatch):
    application = app()
    win, recording, saved = window(monkeypatch)
    win._toggle_dictation()
    win._save()
    saved.assert_not_called()
    recording.stop.assert_called_once()
    recording.text = "A voice note"
    recording.phase = "done"
    win._poll_dictation()
    saved.assert_called_once_with({"id": "screenshot"}, "A voice note", None)


def test_cancel_ignores_late_result(app_env, monkeypatch):
    application = app()
    win, recording, saved = window(monkeypatch)
    win._toggle_dictation()
    win._close()
    recording.cancel.assert_called_once()
    recording.text = "Old note"
    recording.phase = "done"
    win._poll_dictation()
    assert win.text.toPlainText() == ""
    saved.assert_not_called()


def test_error_preserves_note_and_does_not_save(app_env, monkeypatch):
    application = app()
    win, recording, saved = window(monkeypatch)
    win.text.setPlainText("Keep this")
    win._toggle_dictation()
    win._save()
    recording.error = "Microphone unavailable"
    recording.phase = "done"
    win._poll_dictation()
    assert win.text.toPlainText() == "Keep this"
    assert "Microphone unavailable" in win.error.text()
    assert win.mic_btn.isEnabled()
    saved.assert_not_called()


def test_pcm_resampling_and_whistle_output(monkeypatch):
    from checkpoint.audio.dictation import transcribe_pcm
    from checkpoint.transcription import engine
    fake = Mock()
    fake.transcribe.return_value = [{"text": "Hello"}, {"text": "Checkpoint"}]
    monkeypatch.setattr(engine, "WhistleEngine", lambda: fake)
    pcm = np.full((48000, 2), 8192, dtype=np.int16).tobytes()
    assert transcribe_pcm(pcm, 2, 48000) == "Hello Checkpoint"
    audio = fake.transcribe.call_args.args[0]
    assert audio.shape == (16000,)
    assert np.allclose(audio, .25, atol=.001)


def test_dictated_note_persists_with_screenshot(app_env, monkeypatch):
    application = app()
    from test_simple_inbox import captured
    from conftest import restart
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.services.review import get_work_item
    service, cid, _ = captured(app_env)
    wid = ensure_capture(cid)
    win, recording, _ = window(monkeypatch)
    win.payload = {"id": cid}
    def save(payload, text, category):
        service.save_capture_note(payload["id"], text, category)
    win.on_save = save
    win._toggle_dictation()
    win._save()
    recording.text = "The menu covers the controls."
    recording.phase = "done"
    win._poll_dictation()
    restart()
    item = get_work_item(wid)
    assert item["description"] == "The menu covers the controls."
    assert any(e["kind"] == "capture" and e["capture_id"] == cid for e in item["evidence"])
    assert any(e["kind"] == "note" and e["text"] == recording.text for e in item["evidence"])
