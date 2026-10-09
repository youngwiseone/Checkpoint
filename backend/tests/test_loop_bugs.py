"""Regressions in the capture → review loop."""


def _projects():
    from checkpoint.db import write_session
    from checkpoint.models import Project
    from checkpoint.settings_store import update_settings

    with write_session() as s:
        game, other = Project(name="Dodge This"), Project(name="Other")
        s.add_all([game, other])
        s.flush()
        ids = game.id, other.id
    update_settings({"last_project_id": ids[1],
                     "app_watch": {"enabled": True, "rules": [{"project_id": ids[0], "kind": "exe", "match": "dodgethis.exe"}]}})
    return ids


def test_note_uses_selected_project_even_over_a_different_app(app_env, monkeypatch):
    from checkpoint.capture import win32
    from checkpoint.db import read_session
    from checkpoint.models import DraftItem
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager

    game, other = _projects()
    # By the time the note is saved, Checkpoint's own note window is in front.
    monkeypatch.setattr(win32, "foreground_window", lambda: {"exe": "python.exe", "title": "Quick note"})
    pressed_over = {"exe": "DodgeThis.exe", "title": "Dodge This"}
    res = CaptureService(SessionManager()).quick_note("Water kills instantly", window=pressed_over)
    with read_session() as s:
        assert s.get(DraftItem, res["draft_id"]).project_id == other
    # Pressed over something unrelated (or over Checkpoint itself): the last project, as before.
    res = CaptureService(SessionManager()).quick_note("Another note", window=None)
    with read_session() as s:
        assert s.get(DraftItem, res["draft_id"]).project_id == other
