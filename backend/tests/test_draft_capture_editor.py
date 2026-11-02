"""Temporary edit screenshots do not become evidence until their tab is clicked."""
import os
from datetime import datetime, timezone
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabBar
from sqlalchemy import func, select

from conftest import png_bytes
from test_screenshot_editor import at, gesture
from test_simple_inbox import captured


@pytest.fixture
def draft_editor(app_env):
    from checkpoint.desktop.editor import CaptureEditor
    from checkpoint.services.review import get_work_item
    application = QApplication.instance() or QApplication([])
    service, cid, _ = captured(app_env)
    iid = service.save_capture_note(cid, 'Original note')['work_item_id']
    item = get_work_item(iid)
    draft = draft_image(item)
    window = CaptureEditor()
    window.open_for({'item_id': iid, 'draft_capture': draft})
    application.processEvents()
    try:
        yield application, window, iid, cid, draft
    finally:
        window._close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def draft_image(item, color=(180, 60, 130)):
    png, thumb = png_bytes(color=color)
    return {'png': png, 'thumb_jpeg': thumb, 'width': 320, 'height': 180,
            'project_id': item['project_id'], 'session_id': item.get('session_id'),
            'taken_at': datetime.now(timezone.utc), 'offset_ms': None,
            'monitor': {}, 'window_title': 'App being edited'}


def draft_tab(window):
    from checkpoint.desktop.editor import DRAFT_CAPTURE_TAB
    return next(index for index in range(window.image_tabs.count())
                if window.image_tabs.tabData(index) == DRAFT_CAPTURE_TAB)


def capture_count():
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    with read_session() as session:
        return session.scalar(select(func.count(Capture.id)))


def media_files():
    from checkpoint.config import paths
    return {path for path in paths().media.rglob('*') if path.is_file()}


def test_pending_tab_and_refresh_never_write_or_select_temporary_image(draft_editor):
    from checkpoint.services.review import get_work_item
    application, window, iid, cid, draft = draft_editor
    files = media_files()
    assert window.image_tabs.count() == 2
    assert 'Draft' in window.image_tabs.tabText(draft_tab(window))
    assert window.image_tabs.tabData(window.image_tabs.currentIndex()) == cid
    assert set(window.workspaces) == {cid}
    assert 'draft_capture' not in window.payload
    window._reload_evidence(select_new=True)
    window._poll_context()
    application.processEvents()
    assert window._draft_capture is draft
    assert window.image_tabs.tabData(window.image_tabs.currentIndex()) == cid
    # Programmatic/keyboard tab selection must not accept a temporary capture.
    window.image_tabs.setCurrentIndex(draft_tab(window))
    application.processEvents()
    assert window.image_tabs.tabData(window.image_tabs.currentIndex()) == cid
    assert capture_count() == get_work_item(iid)['capture_count'] == 1
    assert media_files() == files


def test_click_accepts_once_then_image_is_normal_editable_evidence(draft_editor, monkeypatch):
    from checkpoint.services import task_evidence
    from checkpoint.services.review import get_work_item
    from checkpoint.storage import media_path
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    application, window, iid, first, draft = draft_editor
    accepted = Mock(wraps=task_evidence.accept_draft_capture)
    monkeypatch.setattr(task_evidence, 'accept_draft_capture', accepted)
    QTest.mouseClick(window.image_tabs, Qt.MouseButton.LeftButton,
                     pos=window.image_tabs.tabRect(draft_tab(window)).center())
    application.processEvents()
    accepted.assert_called_once_with(iid, draft)
    assert window._draft_capture is None
    assert window.image_tabs.count() == 2
    assert capture_count() == get_work_item(iid)['capture_count'] == 2
    cid = window.image_tabs.tabData(window.image_tabs.currentIndex())
    assert cid != first and cid in window.workspaces
    with read_session() as session:
        assert media_path(session.get(Capture, cid).image_rel_path).read_bytes() == draft['png']
    workspace = window.workspaces[cid]
    gesture(workspace.image, at(workspace.image, 20, 30), at(workspace.image, 100, 30))
    assert workspace.dirty and len(workspace.image.marks) == 1
    QTest.mouseClick(window.image_tabs, Qt.MouseButton.LeftButton,
                     pos=window.image_tabs.tabRect(window.image_tabs.currentIndex()).center())
    accepted.assert_called_once()


def test_temporary_image_x_discards_without_confirmation_or_writes(draft_editor, monkeypatch):
    from checkpoint.desktop import editor
    application, window, _, cid, _ = draft_editor
    files = media_files()
    confirmation = Mock(return_value=True)
    monkeypatch.setattr(editor, 'confirm', confirmation)
    close = window.image_tabs.tabButton(draft_tab(window), QTabBar.ButtonPosition.RightSide)
    QTest.mouseClick(close, Qt.MouseButton.LeftButton)
    application.processEvents()
    confirmation.assert_not_called()
    assert window._draft_capture is None
    assert window.image_tabs.count() == 1
    assert window.image_tabs.tabData(0) == cid
    assert capture_count() == 1 and media_files() == files


def test_save_drops_unaccepted_draft_and_saves_only_note(draft_editor):
    from checkpoint.services.review import get_work_item
    _, window, iid, _, _ = draft_editor
    files = media_files()
    window.text.setPlainText('Save this note without the temporary screenshot')
    window._save()
    assert not window.isVisible() and window._draft_capture is None
    assert window.image_tabs.count() == 1
    assert get_work_item(iid)['description'] == 'Save this note without the temporary screenshot'
    assert get_work_item(iid)['capture_count'] == capture_count() == 1
    assert media_files() == files


def test_new_edit_replaces_only_pending_draft_and_retains_note_and_drawings(draft_editor):
    from checkpoint.services.review import get_work_item
    application, window, iid, cid, first_draft = draft_editor
    files = media_files()
    workspace = window.workspaces[cid]
    gesture(workspace.image, at(workspace.image, 20, 30), at(workspace.image, 100, 30))
    window.text.setPlainText('Keep these unsaved edits')
    replacement = draft_image(get_work_item(iid), color=(30, 170, 90))
    assert replacement['png'] != first_draft['png']
    window.open_for({'item_id': iid, 'draft_capture': replacement})
    application.processEvents()
    assert window._draft_capture is replacement
    assert window.image_tabs.count() == 2
    assert window.workspaces[cid] is workspace and len(workspace.image.marks) == 1
    assert workspace.dirty and window.text.toPlainText() == 'Keep these unsaved edits'
    assert get_work_item(iid)['description'] == 'Original note'
    assert capture_count() == 1 and media_files() == files
    window.open_for({'item_id': iid, 'draft_capture': None})
    application.processEvents()
    assert window._draft_capture is None and window.image_tabs.count() == 1
    assert workspace.dirty and window.text.toPlainText() == 'Keep these unsaved edits'


def test_failed_add_restores_saved_tab_and_leaves_draft_retryable(draft_editor, monkeypatch):
    from checkpoint.services import task_evidence
    application, window, _, cid, draft = draft_editor
    original = task_evidence.accept_draft_capture
    monkeypatch.setattr(task_evidence, 'accept_draft_capture', Mock(side_effect=OSError('Disk unavailable')))
    QTest.mouseClick(window.image_tabs, Qt.MouseButton.LeftButton,
                     pos=window.image_tabs.tabRect(draft_tab(window)).center())
    application.processEvents()
    assert window._draft_capture is draft
    assert 'Disk unavailable' in window.error.text()
    assert window.image_tabs.tabData(window.image_tabs.currentIndex()) == cid
    assert capture_count() == 1
    monkeypatch.setattr(task_evidence, 'accept_draft_capture', original)
    QTest.mouseClick(window.image_tabs, Qt.MouseButton.LeftButton,
                     pos=window.image_tabs.tabRect(draft_tab(window)).center())
    application.processEvents()
    assert window._draft_capture is None and capture_count() == 2


def test_copied_task_image_excludes_draft_until_explicit_accept(draft_editor):
    import io
    import json
    from PIL import Image
    from checkpoint.desktop.clipboard import copy_image_files, task_image_files
    application, window, iid, _, _ = draft_editor
    try:
        copy_image_files(task_image_files([iid]))
        with Image.open(io.BytesIO(bytes(application.clipboard().mimeData().data('image/png')))) as image:
            assert json.loads(image.info['Checkpoint.Item'])['image_count'] == 1
        QTest.mouseClick(window.image_tabs, Qt.MouseButton.LeftButton,
                         pos=window.image_tabs.tabRect(draft_tab(window)).center())
        application.processEvents()
        copy_image_files(task_image_files([iid]))
        with Image.open(io.BytesIO(bytes(application.clipboard().mimeData().data('image/png')))) as image:
            assert json.loads(image.info['Checkpoint.Item'])['image_count'] == 2
    finally:
        application.clipboard().clear()
