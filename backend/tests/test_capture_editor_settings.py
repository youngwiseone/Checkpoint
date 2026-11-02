"""Capture editor preferences adopt safe defaults and remain independent."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def test_older_preferences_default_to_showing_capture_editor(app_env):
    from checkpoint.db import write_session
    from checkpoint.models import Setting
    from checkpoint.settings_store import get_settings, invalidate_cache, update_settings

    with write_session() as session:
        session.add(Setting(key='app', value={'version': 6, 'capture': {'auto_copy_latest': False}}))
    assert get_settings().capture.show_editor_on_capture
    assert not get_settings().capture.auto_copy_latest
    update_settings({'capture': {'show_editor_on_capture': False}})
    invalidate_cache()
    assert not get_settings().capture.show_editor_on_capture
    assert not get_settings().capture.auto_copy_latest


def test_native_capture_editor_setting_is_above_clipboard_and_persists(app_env, monkeypatch):
    from PySide6.QtWidgets import QFormLayout
    from checkpoint.settings_store import get_settings, invalidate_cache
    from test_native_desktop import app, settings_dialog

    application = app()
    dialog = settings_dialog(monkeypatch)
    try:
        form = dialog.tabs.widget(0).layout()
        editor_row, editor_role = form.getWidgetPosition(dialog.show_editor_on_capture)
        clipboard_row, clipboard_role = form.getWidgetPosition(dialog.auto_copy_latest)
        assert editor_role == clipboard_role == QFormLayout.ItemRole.SpanningRole
        assert editor_row + 1 == clipboard_row
        assert dialog.show_editor_on_capture.isChecked()
        assert dialog.auto_copy_latest.isChecked()
        dialog.show_editor_on_capture.setChecked(False)
        dialog._save()
    finally:
        dialog.close()
    invalidate_cache()
    assert not get_settings().capture.show_editor_on_capture
    assert get_settings().capture.auto_copy_latest

    dialog = settings_dialog(monkeypatch)
    try:
        assert not dialog.show_editor_on_capture.isChecked()
        dialog.show_editor_on_capture.setChecked(True)
        dialog._save()
    finally:
        dialog.close()
    invalidate_cache()
    assert get_settings().capture.show_editor_on_capture
