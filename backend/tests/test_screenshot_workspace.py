"""Embedded image drafts stay editable until the containing task is saved."""
import os

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture
def workspace(app_env):
    from checkpoint.desktop.screenshot import ScreenshotWorkspace
    from test_simple_inbox import captured

    application = QApplication.instance() or QApplication([])
    _, cid, _ = captured(app_env)
    widget = ScreenshotWorkspace(cid)
    yield widget
    widget.close()
    widget.deleteLater()
    application.processEvents()


def test_embedded_workspace_defaults_to_red_twelve_pixel_marker(workspace):
    assert workspace.tool_buttons['pencil'].isChecked()
    assert workspace.tool_buttons['pencil'].text() == 'Marker'
    assert workspace.image.tool == 'pencil'
    assert workspace.image.color == '#ff4040'
    assert workspace.image.stroke_width == workspace.stroke.value() == 12
    assert not workspace.dirty
    assert workspace.commit() is False


def test_embedded_workspace_draft_is_only_written_on_commit(workspace):
    from checkpoint.db import read_session
    from checkpoint.models import Capture

    before = workspace.expected_sha
    workspace.image.add_text(QPointF(20, 30), 'Mark this')
    assert workspace.dirty
    assert workspace.export_png().startswith(b'\x89PNG')
    with read_session() as session:
        assert session.get(Capture, workspace.capture_id).sha256 == before
    assert workspace.commit() is True
    assert workspace.expected_sha != before
    assert not workspace.dirty
    assert workspace.commit() is False


def test_embedded_workspace_rejects_stale_image(workspace):
    from checkpoint.services.cropping import crop

    workspace.image.add_text(QPointF(20, 30), 'Local draft')
    crop(workspace.capture_id, (0, 0, 80, 60))
    with pytest.raises(ValueError, match='changed elsewhere'):
        workspace.commit()
    assert workspace.dirty
    assert workspace.image.source.width() == 320


def test_shapes_start_as_twelve_pixel_red_arrow_and_highlighter_is_twenty(workspace):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_screenshot_editor import at, gesture

    workspace.resize(1000, 650)
    workspace.show()
    QApplication.processEvents()
    QTest.mouseClick(workspace.tool_buttons['shape'], Qt.MouseButton.LeftButton)
    assert workspace.shape.currentIndex() == 0
    assert workspace.shape.currentData() == workspace.image.tool == 'arrow'
    assert workspace.image.color == '#ff4040'
    assert workspace.image.stroke_width == workspace.stroke.value() == 12
    gesture(workspace.image, at(workspace.image, 20, 30), at(workspace.image, 100, 30))
    assert workspace.image.marks[-1].tool == 'arrow'
    assert workspace.image.marks[-1].width == 12

    QTest.mouseClick(workspace.tool_buttons['highlighter'], Qt.MouseButton.LeftButton)
    assert workspace.image.stroke_width == workspace.stroke.value() == 20
    gesture(workspace.image, at(workspace.image, 20, 80), at(workspace.image, 100, 80))
    assert workspace.image.marks[-1].tool == 'highlighter'
    assert workspace.image.marks[-1].width == 20

    # Switching tools retains the shape and width explicitly chosen by the user.
    QTest.mouseClick(workspace.tool_buttons['shape'], Qt.MouseButton.LeftButton)
    workspace.shape.setCurrentIndex(workspace.shape.findData('ellipse'))
    workspace.stroke.setValue(16)
    QTest.mouseClick(workspace.tool_buttons['pencil'], Qt.MouseButton.LeftButton)
    QTest.mouseClick(workspace.tool_buttons['shape'], Qt.MouseButton.LeftButton)
    assert workspace.image.tool == 'ellipse'
    assert workspace.image.stroke_width == workspace.stroke.value() == 16


@pytest.mark.parametrize('key_name', ['Key_Return', 'Key_Enter'])
def test_enter_applies_pending_crop_without_saving_and_keeps_undo(workspace, key_name):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from test_screenshot_editor import at, gesture

    workspace.resize(1000, 650)
    workspace.show()
    QApplication.processEvents()
    original_view = workspace.image.view.getRect()
    original_sha = workspace.expected_sha
    QTest.mouseClick(workspace.tool_buttons['crop'], Qt.MouseButton.LeftButton)
    gesture(workspace.image, at(workspace.image, 30, 20), at(workspace.image, 180, 120))
    assert workspace.image.hasFocus()
    assert workspace.image.selection is not None
    QTest.keyClick(workspace.image, getattr(Qt.Key, key_name))
    assert workspace.image.selection is None
    assert workspace.image.view.getRect() != original_view
    assert workspace.dirty
    with read_session() as session:
        assert session.get(Capture, workspace.capture_id).sha256 == original_sha
    workspace.image.undo()
    assert workspace.image.view.getRect() == original_view
    assert not workspace.dirty
