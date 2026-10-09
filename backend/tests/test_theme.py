"""Night blue by default, the light card look as an option: every native window follows the setting."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_palettes_match():
    from checkpoint import theme

    dark, light = theme.PALETTES["dark"], theme.PALETTES["light"]
    assert dark.keys() == light.keys()
    for value in [*dark.values(), *light.values()]:
        assert len(theme.rgba(value)) == 4
    assert theme.rgba("#12345678") == (0x12, 0x34, 0x56, 0x78)  # #rrggbbaa, not Qt's #aarrggbb


def test_default_theme_is_dark(app_env):
    from checkpoint import theme
    from checkpoint.desktop import colors

    assert theme.current() == "dark"
    assert colors.current().dark
    assert colors.qcolor("pill").alpha() == theme.rgba(theme.PALETTES["dark"]["pill"])[3]


@pytest.mark.parametrize("name", ["dark", "light"])
def test_windows_render_in_each_theme(app_env, name):
    from PySide6.QtWidgets import QApplication

    from checkpoint.desktop import colors
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.desktop.windows import NoteWindow, Toast
    from checkpoint.services import review as rv
    from checkpoint.settings_store import update_settings
    from checkpoint.services.capture import default_project_id

    app = QApplication.instance() or QApplication([])
    update_settings({"appearance": {"theme": name}})
    c = colors.current()
    assert c.name == name
    pid = default_project_id()
    rv.create_work_item(pid, "bug", "Something broke", "")

    ov = InboxPanel(lambda *args: None)
    ov.toggle(pid)
    app.processEvents()
    assert not ov.grab().isNull()
    assert c.text.name() in ov.styleSheet()

    note = NoteWindow(lambda *a: None, lambda *a: None, lambda *a: None)
    note._apply_theme()
    assert note._theme == name
    assert c.text.name() in note.styleSheet()
    assert not note.grab().isNull()
    toast = Toast()
    toast.label.setText("Saved")
    assert not toast.grab().isNull()

    # Switching applies the next time a window renders, no restart.
    other = "light" if name == "dark" else "dark"
    update_settings({"appearance": {"theme": other}})
    ov.hide()
    ov.toggle(pid)
    assert colors.current().text.name() in ov.styleSheet()
    for w in (ov, note, toast):
        w.close()
    del app
