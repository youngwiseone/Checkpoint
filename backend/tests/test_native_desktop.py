import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_inbox_processing_progress_and_attention(app_env, monkeypatch):
    application = app()
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.transcription import worker
    queried = []
    def counts(session_id):
        queried.append(session_id)
        return {"done": 3, "running": 1, "queued": 2, "failed": 1}
    monkeypatch.setattr(worker, "transcription_status", counts)
    state = {"paused": False, "model_installed": True}
    panel = InboxPanel(processing_status=lambda: state)
    panel._refresh_processing({"session_id": "active-session"})
    assert queried == ["active-session"]
    assert panel.processing_progress.maximum() == 7
    assert panel.processing_progress.value() == 3
    assert "3 of 7 clips complete" in panel.processing_label.text()
    assert "2 waiting" in panel.processing_label.text()
    assert "1 failed" in panel.processing_label.text()
    state["paused"] = True
    panel._refresh_processing({})
    assert "Transcription paused" in panel.processing_label.text()
    state.update(paused=False, error="Model could not load")
    panel._refresh_processing({})
    assert "needs attention" in panel.processing_label.text()
    assert "Model could not load" in panel.processing_label.text()
    panel.close()


def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_hold_menu_starts_and_replaces_session_for_selected_project(app_env):
    from types import SimpleNamespace
    from checkpoint.desktop.host import DesktopHost
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.sessions import SessionManager
    from checkpoint.db import read_session
    from checkpoint.models import Session

    application = app()
    manager = SessionManager()
    desktop = DesktopHost.__new__(DesktopHost)
    desktop.core = SimpleNamespace(sessions=manager)
    desktop._refresh_tray = Mock()
    panel = InboxPanel(new_session=desktop._new_session_from_panel, recording_status=manager.status)
    panel.project_id = default_project_id()
    panel._load_projects()
    panel.refresh()
    try:
        panel._new_session()
        first = manager.active.id
        assert manager.active.project_id == panel.project_id
        panel._new_session()
        assert manager.active.id != first
        assert manager.active.project_id == panel.project_id
        with read_session() as session:
            assert session.get(Session, first).state == 'ended'
        assert panel.session_picker.findData(manager.active.id) >= 0
        assert 'New session started' in panel.message.text()
    finally:
        manager.shutdown()
        panel.close()


def settings_dialog(monkeypatch):
    from checkpoint import integrations
    from checkpoint.audio import devices
    from checkpoint.desktop.settings import SettingsDialog
    monkeypatch.setattr(integrations, "status", lambda: [])
    monkeypatch.setattr(devices, "list_devices", lambda: {"mic": [], "loopback": []})
    return SettingsDialog()


def test_native_settings_save_and_invalid_keys(app_env, monkeypatch):
    application = app()
    from checkpoint.services.capture import default_project_id
    from checkpoint.settings_store import get_settings
    from PySide6.QtWidgets import QDialog
    pid = default_project_id()
    dialog = settings_dialog(monkeypatch)
    dialog.project.setCurrentIndex(dialog.project.findData(pid))
    dialog.capture_key.setText("F7")
    dialog.mic.setChecked(True)
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    saved = get_settings()
    assert saved.hotkeys.capture == "F7" and saved.hotkeys.review == ""
    assert saved.audio.mic_enabled and saved.appearance.theme == "light"
    assert saved.last_project_id == pid
    dialog.close()
    dialog = settings_dialog(monkeypatch)
    dialog.capture_key.setText("invalid-key")
    dialog._save()
    assert "Unknown key" in dialog.message.text()
    assert get_settings().hotkeys.review == ""
    dialog.close()


def test_native_task_save_rejects_conflicting_edit(app_env):
    application = app()
    from checkpoint.desktop.editor import TaskDialog
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item, get_work_item, update_work_item
    item = create_work_item(default_project_id(), "bug", "Old title", "")
    dialog = TaskDialog(item["id"])
    dialog.title.setText("New title")
    dialog.description.setPlainText("Expected behaviour")
    dialog._save()
    assert get_work_item(item["id"])["title"] == "New title"
    dialog.close()
    dialog = TaskDialog(item["id"])
    update_work_item(item["id"], {"description": "Changed elsewhere"})
    dialog._save()
    assert "changed elsewhere" in dialog.message.text()
    assert get_work_item(item["id"])["description"] == "Changed elsewhere"
    dialog.close()


def test_native_selection_maps_letterboxed_image_and_crop(app_env):
    application = app()
    from PySide6.QtCore import QRect
    from checkpoint.desktop.editor import ScreenshotDialog
    from checkpoint.services.cropping import crop
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from test_simple_inbox import captured
    _, cid, _ = captured(app_env)
    dialog = ScreenshotDialog(cid)
    dialog.image.resize(640, 640)
    rect = dialog.image.image_rect()
    assert rect.top() > 0
    dialog.image.selection = QRect(rect.left() + 20, rect.top() + 40, 200, 160)
    assert dialog.image.box() == (10, 20, 100, 80)
    dialog._save()
    with read_session() as s:
        capture = s.get(Capture, cid)
        assert (capture.width, capture.height) == (100, 80)
    dialog.close()
    crop(cid, None)


def test_note_window_context_menu_can_edit_screenshot(app_env, monkeypatch):
    application = app()
    from checkpoint.desktop import editor
    from checkpoint.desktop.windows import NoteWindow

    opened = []

    class FakeScreenshotDialog:
        def __init__(self, capture_id, parent):
            opened.append((capture_id, parent))

        def exec(self):
            return 0

    monkeypatch.setattr(editor, "ScreenshotDialog", FakeScreenshotDialog)
    monkeypatch.setattr(editor, "capture_path", lambda capture_id: "missing.png")
    window = NoteWindow(Mock(), Mock(), Mock())
    window.open_for({"id": "capture-1", "thumb_path": "missing.png"})
    assert window.edit_image_btn.isVisible()
    window.text.setPlainText("Remember this detail")
    window.edit_image_btn.click()
    assert opened == [("capture-1", window)]
    assert window.text.toPlainText() == "Remember this detail"
    window.open_for({})
    assert not window.edit_image_btn.isVisible()
    window.close()


def test_desktop_open_uses_native_panel(app_env, monkeypatch):
    application = app()
    import webbrowser
    from checkpoint.desktop import host
    from checkpoint.services.capture import default_project_id
    from checkpoint.settings_store import update_settings
    update_settings({"last_project_id": default_project_id()})
    core = Mock()
    core.sessions.active = None
    core.sessions.status.return_value = {"active": False}
    core.worker_status.return_value = {"transcription": {"paused": False, "model_installed": True}}
    monkeypatch.setattr(host, "get_core", lambda: core)
    browser = Mock()
    monkeypatch.setattr(webbrowser, "open", browser)
    desktop = host.DesktopHost()
    desktop.open_ui()
    assert desktop.overlay.isVisible()
    assert not hasattr(desktop, "server")
    browser.assert_not_called()
    desktop.overlay.close()
    desktop.note_window.hide()
    desktop.toast.hide()


def test_transcript_selection_creates_task_with_evidence(app_env):
    application = app()
    from checkpoint.db import write_session
    from checkpoint.models import AudioSource, Session, TranscriptSegment, new_id
    from checkpoint.desktop.history import HistoryDialog
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import get_work_item, list_work_items
    pid = default_project_id()
    with write_session() as s:
        session = Session(project_id=pid, title="Playtest", state="ended")
        s.add(session); s.flush()
        source = AudioSource(session_id=session.id, kind="mic", label="Me")
        s.add(source); s.flush()
        s.add(TranscriptSegment(id=new_id(), session_id=session.id, source_id=source.id,
                                start_ms=1000, end_ms=2000, text="The menu overlaps", corrected_text="The menu overlaps the controls"))
    dialog = HistoryDialog(pid)
    dialog.lines.item(0).setSelected(True)
    dialog._task()
    item = get_work_item(list_work_items(pid)[0]["id"])
    assert item["title"] == "The menu overlaps the controls"
    assert item["evidence"][0]["raw_text"] == "The menu overlaps"
    dialog.close()
