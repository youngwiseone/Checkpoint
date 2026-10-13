"""Card copy keeps complete PNGs and the entire selection on the clipboard."""
import io
import json
import os
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def wait_for_copy(app, panel):
    from PySide6.QtTest import QTest
    for _ in range(800):
        app.processEvents()
        if not panel.deck.copying:
            return
        QTest.qWait(10)
    pytest.fail('Task image copy did not finish')


def test_copy_button_copies_full_task_png_and_latest_note(app_env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services import image_cards, review
    from test_simple_inbox import captured

    app = QApplication.instance() or QApplication([])
    service, cid, _ = captured(app_env)
    iid = service.save_capture_note(cid, 'Old note')['work_item_id']
    review.update_work_item(iid, {'description': 'Latest edited note'})
    panel = InboxPanel()
    panel.toggle(review.get_work_item(iid)['project_id'])
    edited = Mock()
    panel.deck.expand.connect(edited)
    try:
        assert panel.deck.copy_button.objectName() == panel.deck.edit_button.objectName()
        assert panel.deck.copy_button.size() == panel.deck.edit_button.size()
        assert panel.deck.copy_button.geometry().right() < panel.deck.edit_button.x()
        assert not panel.deck.copy_button.icon().isNull()
        QTest.mouseClick(panel.deck.copy_button, Qt.MouseButton.LeftButton)
        wait_for_copy(app, panel)
        mime = app.clipboard().mimeData()
        assert mime.hasImage() and mime.hasUrls()
        assert len(mime.urls()) == 1
        png = bytes(mime.data('image/png'))
        path = Path(mime.urls()[0].toLocalFile())
        assert path.read_bytes() == png
        with Image.open(io.BytesIO(png)) as image:
            assert image.info['Checkpoint.Context'].count('Latest edited note') == 1
            assert 'Old note' not in image.info['Checkpoint.Context']
            assert json.loads(image.info['Checkpoint.Item'])['image_count'] == 1
            assert image.getpixel((500, 64)) == (40, 90, 160)
            assert mime.imageData().width() == image.width
            assert mime.imageData().height() == image.height
        assert panel.selection == {iid} and panel.selected_id == iid
        edited.assert_not_called()
        assert panel.isVisible() and panel.message.text() == 'Task image copied.'
        review.update_work_item(iid, {'description': 'Changed after copying', 'work_status': 'done'})
        image_cards.refresh()
        assert path.read_bytes() == png
    finally:
        app.clipboard().clear()
        panel.close()


def test_copy_includes_all_selected_and_excludes_deselected_focus(app_env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from checkpoint.services.review import get_work_item, update_work_item
    from test_batch_review import panel_with_cards, settle

    app, panel, ids = panel_with_cards()
    try:
        panel._select_cards(3, Qt.KeyboardModifier.ShiftModifier)
        panel._select_cards(-1, Qt.KeyboardModifier.ControlModifier)
        settle(panel.deck)
        assert panel.selected_id == ids[2]
        assert panel.selection == {ids[0], ids[1], ids[3]}
        assert panel.deck.copy_button.toolTip() == 'Copy 3 task images'
        QTest.mouseClick(panel.deck.copy_button, Qt.MouseButton.LeftButton)
        # Moving focus while PNGs render must keep the clicked selection.
        panel._select_cards(1, Qt.KeyboardModifier.NoModifier)
        wait_for_copy(app, panel)
        mime = app.clipboard().mimeData()
        assert mime.hasUrls() and not mime.hasImage()
        paths = [Path(url.toLocalFile()) for url in mime.urls()]
        assert len(paths) == 3
        copied = []
        for path in paths:
            with Image.open(path) as image:
                item = json.loads(image.info['Checkpoint.Item'])['item']
                copied.append(item['id'])
                assert get_work_item(item['id'])['description'] in image.info['Checkpoint.Context']
        assert copied == [ids[0], ids[1], ids[3]]
        assert panel.message.text() == '3 task images copied.'
        assert all(get_work_item(iid)['work_status'] == 'open' for iid in ids)
        update_work_item(ids[0], {'description': 'New note after copying'})
        with Image.open(paths[0]) as image:
            assert 'New note after copying' not in image.info['Checkpoint.Context']
    finally:
        app.clipboard().clear()
        panel.close()


def test_copy_failure_keeps_existing_clipboard_and_allows_retry(app_env, monkeypatch):
    from checkpoint.desktop import clipboard
    from test_batch_review import panel_with_cards

    app, panel, _ = panel_with_cards()
    app.clipboard().setText('Keep the previous clipboard')
    def failed(_):
        raise OSError('Cannot write task image')
    monkeypatch.setattr(clipboard, 'task_image_files', failed)
    try:
        panel.deck.copy_button.click()
        wait_for_copy(app, panel)
        assert app.clipboard().text() == 'Keep the previous clipboard'
        assert 'Cannot write task image' in panel.message.text()
        assert panel.deck.copy_button.isEnabled()
        panel.selection.clear()
        panel._present()
        assert not panel.deck.copy_button.isEnabled()
    finally:
        app.clipboard().clear()
        panel.close()
