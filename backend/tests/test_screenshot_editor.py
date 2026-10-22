"""Exercise actual pointer gestures, preview pixels, and the saved image pipeline."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_simple_inbox import captured


@pytest.fixture(scope='session')
def editor_application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(app_env, editor_application):
    from checkpoint.desktop.editor import ScreenshotDialog
    application = editor_application
    existing_windows = set(application.topLevelWidgets())
    _, cid, _ = captured(app_env)
    dialog = ScreenshotDialog(cid)
    dialog.show()
    application.processEvents()
    yield dialog
    for window in set(application.topLevelWidgets()) - existing_windows:
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def gesture(canvas, a, b):
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(canvas, b)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=b)


def at(canvas, x, y):
    rect = canvas.image_rect()
    return QPoint(round(rect.x() + (x - canvas.view.x()) * rect.width() / canvas.view.width()),
                  round(rect.y() + (y - canvas.view.y()) * rect.height() / canvas.view.height()))


def test_pencil_highlighter_eraser_and_history(editor):
    canvas = editor.image
    background = canvas.render_image().pixelColor(50, 30)
    gesture(canvas, at(canvas, 20, 30), at(canvas, 100, 30))
    assert canvas.render_image().pixelColor(50, 30).red() > 200
    editor.set_tool('eraser')
    gesture(canvas, at(canvas, 50, 20), at(canvas, 50, 40))
    assert canvas.render_image().pixelColor(50, 30) == background
    canvas.undo()
    assert len(canvas.marks) == 1
    canvas.redo()
    assert canvas.marks == []
    editor.set_tool('highlighter')
    gesture(canvas, at(canvas, 20, 60), at(canvas, 100, 60))
    color = canvas.render_image().pixelColor(50, 60)
    assert background.red() < color.red() < 255
    assert background.green() < color.green() < 255
    assert 0 < color.blue() < background.blue()


def test_shapes_text_crop_zoom_and_undo(editor):
    canvas = editor.image
    editor.set_tool('shape')
    for index in range(editor.shape.count()):
        editor.shape.setCurrentIndex(index)
        gesture(canvas, at(canvas, 20, 20), at(canvas, 100, 80))
    assert [mark.tool for mark in canvas.marks] == ['rectangle', 'ellipse', 'line', 'arrow']
    canvas.add_text(QPointF(110, 20), 'A note\nSecond line')
    assert canvas.render_image().copy(110, 20, 150, 70) != canvas.source.toImage().copy(110, 20, 150, 70)
    editor.set_tool('crop')
    before_scale = canvas.image_rect().width() / canvas.view.width()
    gesture(canvas, at(canvas, 10, 10), at(canvas, 160, 100))
    assert editor.crop_button.isEnabled()
    editor.apply_crop()
    QApplication.processEvents()
    assert canvas.view.width() < 160
    assert canvas.image_rect().width() / canvas.view.width() > before_scale
    assert len(canvas.marks) == 5
    saved_view = QRect(canvas.view)
    editor.zoom_by(1.25)
    assert not editor.scroll.widgetResizable()
    assert canvas.view == saved_view
    editor.fit()
    canvas.undo()
    assert canvas.view == canvas.source.rect()
    canvas.redo()
    assert canvas.view == saved_view


def test_save_refreshes_original_thumbnail_handoff_and_restore(editor):
    from PIL import Image
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from checkpoint.services.cropping import original_path
    from checkpoint.services.handoff import build
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.storage import media_path
    from checkpoint.desktop.editor import ScreenshotDialog
    cid = editor.capture_id
    wid = ensure_capture(cid)
    with read_session() as session:
        capture = session.get(Capture, cid)
        original = media_path(capture.image_rel_path).read_bytes()
    canvas = editor.image
    gesture(canvas, at(canvas, 10, 30), at(canvas, 100, 30))
    editor.set_tool('crop')
    gesture(canvas, at(canvas, 0, 0), at(canvas, 160, 100))
    editor.apply_crop()
    expected = canvas.render_image()
    editor._save()
    assert editor.result() == editor.DialogCode.Accepted
    with read_session() as session:
        capture = session.get(Capture, cid)
        assert original_path(capture).read_bytes() == original
        assert (capture.width, capture.height) == (expected.width(), expected.height())
        with Image.open(media_path(capture.image_rel_path)) as image:
            assert image.getpixel((50, 30))[0] > 200
        with Image.open(media_path(capture.thumb_rel_path)) as thumb:
            assert thumb.size == image.size
    with Image.open(build([wid], True, True).images[0][1]) as shared:
        assert shared.size == (expected.width(), expected.height())
    reopened = ScreenshotDialog(cid)
    assert reopened.image.source.width() == expected.width()
    reopened.restore_original()
    assert reopened.image.source.size().width() == 320
    reopened.image.undo()
    assert reopened.image.source.width() == expected.width()
    reopened.image.redo()
    reopened._save()
    with Image.open(build([wid], True, True).images[0][1]) as shared:
        assert shared.size == (320, 180)
    reopened.close()


def test_cancel_does_not_write_and_stale_save_is_rejected(editor):
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from checkpoint.services.cropping import crop
    from checkpoint.desktop.editor import ScreenshotDialog
    cid = editor.capture_id
    canvas = editor.image
    gesture(canvas, at(canvas, 10, 30), at(canvas, 100, 30))
    editor.reject()
    with read_session() as session:
        assert session.get(Capture, cid).sha256 == editor.expected_sha
    stale = ScreenshotDialog(cid)
    crop(cid, (0, 0, 80, 60))
    stale._save()
    assert 'changed elsewhere' in stale.message.text()
    with read_session() as session:
        assert session.get(Capture, cid).width == 80
    stale.close()


def test_zoomed_drawing_and_second_crop_use_current_image_coordinates(editor):
    canvas = editor.image
    editor.set_tool('crop')
    gesture(canvas, at(canvas, 50, 20), at(canvas, 200, 120))
    editor.apply_crop()
    QApplication.processEvents()
    editor.zoom_by(1.25)
    editor.set_tool('pencil')
    canvas.color = '#00ff00'
    editor.stroke.setValue(5)
    gesture(canvas, at(canvas, 70, 50), at(canvas, 130, 50))
    image = canvas.render_image()
    assert image.pixelColor(100 - canvas.view.x(), 50 - canvas.view.y()).green() == 255
    editor.set_tool('eraser')
    editor.set_tool('pencil')
    assert canvas.color == '#00ff00'
    assert canvas.stroke_width == 5
    editor.set_tool('crop')
    gesture(canvas, at(canvas, 60, 30), at(canvas, 150, 100))
    editor.apply_crop()
    assert canvas.view.x() == pytest.approx(60, abs=1)
    assert canvas.view.y() == pytest.approx(30, abs=1)
    image = canvas.render_image()
    assert image.pixelColor(100 - canvas.view.x(), 50 - canvas.view.y()).green() == 255
    canvas.undo()
    assert canvas.view.x() == pytest.approx(50, abs=1)


def test_main_task_uses_same_screenshot_editor(editor, monkeypatch):
    from checkpoint.desktop import editor as task_editor
    from checkpoint.services.inbox import ensure_capture
    from unittest.mock import Mock
    wid = ensure_capture(editor.capture_id)
    dialog = task_editor.TaskDialog(wid)
    opened = Mock(return_value=Mock())
    monkeypatch.setattr(task_editor, 'ScreenshotDialog', opened)
    dialog._edit_image()
    opened.assert_called_once_with(editor.capture_id, dialog)
    opened.return_value.exec.assert_called_once()
    dialog.close()
