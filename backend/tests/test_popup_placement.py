"""Popups follow the pointer across displays, including negative screen origins."""
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture
def displays(monkeypatch):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QApplication
    from checkpoint.desktop import windows

    app = QApplication.instance() or QApplication([])
    primary_area = QRect(0, 0, 1600, 960)
    secondary_area = QRect(-1920, -120, 1920, 1040)
    primary = SimpleNamespace(availableGeometry=lambda: primary_area)
    secondary = SimpleNamespace(availableGeometry=lambda: secondary_area)
    pointer = [QPoint(-1000, 200)]
    monkeypatch.setattr(windows.QCursor, 'pos', lambda: pointer[0])
    monkeypatch.setattr(windows.QGuiApplication, 'screenAt',
                        lambda point: secondary if secondary_area.contains(point)
                        else primary if primary_area.contains(point) else None)
    monkeypatch.setattr(windows.QGuiApplication, 'primaryScreen', lambda: primary)
    return app, pointer, primary_area, secondary_area


def test_notes_and_toasts_follow_mouse_instead_of_capture_screen(app_env, displays):
    from PySide6.QtCore import Qt
    from checkpoint.desktop.windows import NoteWindow, Toast

    app, pointer, primary, secondary = displays
    window = NoteWindow(Mock(), Mock(), Mock())
    toast = Toast()
    old_work = {'left': primary.x(), 'top': primary.y(),
                'width': primary.width(), 'height': primary.height()}
    try:
        for area in (secondary, primary):
            pointer[0] = area.center()
            window.open_for({'work_rect': old_work})
            toast.show_message('success', 'Note saved', old_work)
            app.processEvents()
            assert window.geometry().right() == area.right() - 25
            assert window.geometry().bottom() == area.bottom() - 25
            assert toast.geometry().right() == area.right() - 21
            assert toast.geometry().bottom() == area.bottom() - 21
            assert toast.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
            window.hide()
            toast.hide()
    finally:
        window.hide()
        toast.hide()


def test_review_and_child_dialog_open_on_mouse_screen(app_env, displays):
    from PySide6.QtWidgets import QWidget
    from checkpoint.desktop.glass import GlassDialog
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id

    app, pointer, primary, secondary = displays
    panel = InboxPanel()
    parent = QWidget()
    parent.setGeometry(primary.adjusted(100, 100, -100, -100))
    parent.show()
    dialog = GlassDialog(parent)
    dialog.resize(430, 240)
    try:
        for area in (secondary, primary):
            pointer[0] = area.center()
            panel.toggle(default_project_id())
            dialog.show()
            app.processEvents()
            assert panel.geometry() == area.adjusted(12, 12, -12, -12)
            assert (dialog.geometry().center() - area.center()).manhattanLength() <= 2
            panel.hide()
            dialog.hide()
    finally:
        panel.close()
        dialog.close()
        parent.close()


def test_mouse_outside_displays_falls_back_to_primary(displays):
    from PySide6.QtCore import QPoint
    from checkpoint.desktop.windows import screen_under_mouse

    _, pointer, primary, _ = displays
    pointer[0] = QPoint(10000, 10000)
    assert screen_under_mouse().availableGeometry() == primary
