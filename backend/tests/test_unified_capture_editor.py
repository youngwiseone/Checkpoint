"""One capture window retains image drafts and saves the whole task together."""
import os
from datetime import datetime, timezone
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from conftest import png_bytes
from test_screenshot_editor import at, gesture
from test_simple_inbox import captured


@pytest.fixture(scope='session')
def capture_application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def capture_dialog(app_env, capture_application):
    from checkpoint.desktop.editor import TaskDialog
    service, cid, _ = captured(app_env)
    iid = service.save_capture_note(cid, 'Original note')['work_item_id']
    existing = set(capture_application.topLevelWidgets())
    dialog = TaskDialog(iid)
    dialog.show()
    capture_application.processEvents()
    yield capture_application, dialog, service, cid, iid
    for window in set(capture_application.topLevelWidgets()) - existing:
        if hasattr(window, '_close'):
            window._close()
        else:
            window.hide()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def append_image(service, iid, color=(50, 120, 70)):
    from checkpoint.services.review import get_work_item
    item = get_work_item(iid)
    png, thumb = png_bytes(color=color)
    return service.import_image(png, thumb, 320, 180,
        project_id=item['project_id'], session_id=item.get('session_id'),
        offset_ms=None, taken_at=datetime.now(timezone.utc), status='saved',
        trigger='snip', attach_to=iid)


def select_image(application, dialog, cid):
    for index in range(dialog.image_tabs.count()):
        if dialog.image_tabs.tabData(index) == cid:
            dialog.image_tabs.setCurrentIndex(index)
            application.processEvents()
            return dialog.workspaces[cid]
    pytest.fail('The captured image has no editor tab')


def test_task_and_hotkey_entries_share_the_unified_editor(capture_dialog):
    from checkpoint.desktop.editor import CaptureEditor
    from checkpoint.desktop.windows import NoteWindow
    from checkpoint.services.task_evidence import capture_editor_payload
    application, task, _, cid, _ = capture_dialog
    quick = NoteWindow(Mock(return_value=None), Mock(), Mock(return_value=None))
    try:
        quick.open_for(capture_editor_payload(cid))
        application.processEvents()
        assert isinstance(task, CaptureEditor) and isinstance(quick, CaptureEditor)
        for dialog in (task, quick):
            assert dialog.image_tabs.count() == 1
            assert dialog.description is dialog.text
            assert not hasattr(dialog, 'category')
            assert dialog.save_btn.isVisible() and dialog.discard_btn.isVisible()
            workspace = select_image(application, dialog, cid)
            assert workspace.image.tool == 'pencil'
            assert workspace.tool_buttons['pencil'].isChecked()
            assert workspace.image.stroke_width == 12
            color = QColor(workspace.image.color)
            assert color.red() == 255 and color.green() < 80 and color.blue() < 80
    finally:
        quick._close()
        quick.deleteLater()


def test_tabs_preserve_drafts_and_save_every_image_with_the_note(capture_dialog):
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from checkpoint.services.review import get_work_item
    from checkpoint.storage import media_path
    application, dialog, service, first, iid = capture_dialog
    second = append_image(service, iid)
    dialog._reload_evidence()
    assert dialog.image_tabs.count() == 2
    with read_session() as session:
        original_shas = {cid: session.get(Capture, cid).sha256 for cid in (first, second)}

    first_workspace = select_image(application, dialog, first)
    gesture(first_workspace.image, at(first_workspace.image, 20, 30), at(first_workspace.image, 100, 30))
    second_workspace = select_image(application, dialog, second)
    second_workspace.image.color = '#00ff00'
    gesture(second_workspace.image, at(second_workspace.image, 20, 60), at(second_workspace.image, 100, 60))
    dialog.text.setPlainText('Both screenshots show the same problem')

    dialog._reload_evidence()
    assert select_image(application, dialog, first) is first_workspace
    assert len(first_workspace.image.marks) == len(second_workspace.image.marks) == 1
    assert dialog.text.toPlainText() == 'Both screenshots show the same problem'
    with read_session() as session:
        assert all(session.get(Capture, cid).sha256 == original_shas[cid] for cid in (first, second))

    dialog._save()
    assert not dialog.isVisible()
    assert get_work_item(iid)['description'] == 'Both screenshots show the same problem'
    with read_session() as session:
        with Image.open(media_path(session.get(Capture, first).image_rel_path)) as image:
            assert image.getpixel((60, 30))[0] > 200
        with Image.open(media_path(session.get(Capture, second).image_rel_path)) as image:
            assert image.getpixel((60, 60))[1] == 255


def test_image_close_requires_confirmation_and_preserves_other_images(capture_dialog, monkeypatch):
    from checkpoint.desktop import editor
    from checkpoint.services.review import get_work_item
    application, dialog, service, first, iid = capture_dialog
    second = append_image(service, iid)
    dialog._reload_evidence()
    select_image(application, dialog, first)
    confirmation = Mock(return_value=False)
    monkeypatch.setattr(editor, 'confirm', confirmation)
    dialog._remove_image(dialog.image_tabs.currentIndex())
    confirmation.assert_called_once()
    assert dialog.image_tabs.count() == 2
    assert get_work_item(iid)['capture_count'] == 2

    confirmation.return_value = True
    dialog._remove_image(dialog.image_tabs.currentIndex())
    assert dialog.image_tabs.count() == 1
    assert dialog.image_tabs.tabData(0) == second
    dialog._save()
    captures = [entry['capture_id'] for entry in get_work_item(iid)['evidence'] if entry['kind'] == 'capture']
    assert captures == [second]


def test_plus_takes_another_screenshot_and_keeps_the_current_draft(capture_dialog, monkeypatch):
    from checkpoint.desktop import snipping
    from checkpoint.services.review import get_work_item
    application, dialog, service, first, iid = capture_dialog
    workspace = select_image(application, dialog, first)
    gesture(workspace.image, at(workspace.image, 20, 30), at(workspace.image, 100, 30))
    dialog.text.setPlainText('Keep this note while taking another screenshot')
    added = []

    def snip(item_id, parent, done):
        assert item_id == iid and parent is dialog
        added.append(append_image(service, iid))
        done(None)

    mocked = Mock(side_effect=snip)
    monkeypatch.setattr(snipping, 'snip_into', mocked)
    dialog.add_image_btn.click()
    application.processEvents()
    mocked.assert_called_once()
    assert dialog.image_tabs.count() == 2
    assert get_work_item(iid)['capture_count'] == 2
    assert dialog.text.toPlainText() == 'Keep this note while taking another screenshot'
    assert select_image(application, dialog, first) is workspace
    assert len(workspace.image.marks) == 1
    select_image(application, dialog, added[0])


def test_discard_drops_unsaved_image_edits_and_discards_the_task(capture_dialog, monkeypatch):
    from checkpoint.desktop import editor
    from checkpoint.db import read_session
    from checkpoint.models import Capture
    from checkpoint.services.review import get_work_item
    application, dialog, _, cid, iid = capture_dialog
    with read_session() as session:
        original_sha = session.get(Capture, cid).sha256
    workspace = select_image(application, dialog, cid)
    gesture(workspace.image, at(workspace.image, 20, 30), at(workspace.image, 100, 30))
    dialog.text.setPlainText('This edit should be discarded')
    monkeypatch.setattr(editor, 'confirm', Mock(return_value=True))
    dialog._discard()
    assert not dialog.isVisible()
    item = get_work_item(iid)
    assert item['work_status'] in ('deleted', 'wont_do')
    assert item['description'] == 'Original note'
    with read_session() as session:
        assert session.get(Capture, cid).sha256 == original_sha


def test_session_speech_arriving_during_edit_preserves_note_and_drawing(app_env, capture_application):
    from checkpoint.db import read_session, write_session
    from checkpoint.desktop.editor import TaskDialog
    from checkpoint.models import Capture
    from checkpoint.services.inbox import ensure_capture, reconcile
    from checkpoint.services.review import get_work_item
    from checkpoint.storage import media_path
    from test_note_workflow import auto_observation

    cid, _, _ = auto_observation(app_env, text='The menu overlaps the controls')
    with write_session() as session:
        session.get(Capture, cid).trigger = 'hotkey'
    iid = ensure_capture(cid)
    dialog = TaskDialog(iid)
    dialog.show()
    capture_application.processEvents()
    try:
        workspace = select_image(capture_application, dialog, cid)
        dialog.text.setPlainText('My typed context while recording')
        gesture(workspace.image, at(workspace.image, 20, 30), at(workspace.image, 100, 30))
        original_version = dialog.item['version']

        reconcile()
        assert not get_work_item(iid)['description_edited']
        assert get_work_item(iid)['version'] > original_version
        dialog._poll_context()
        combined = dialog.text.toPlainText()
        assert combined.startswith('My typed context while recording')
        assert combined.count('The menu overlaps the controls') == 1
        assert dialog.workspaces[cid] is workspace
        assert len(workspace.image.marks) == 1

        # A later poll must keep typing made after the first transcript update.
        dialog.text.appendPlainText('More context after the speech arrived')
        expected = dialog.text.toPlainText()
        dialog._poll_context()
        assert dialog.text.toPlainText() == expected
        dialog._save()
        assert not dialog.isVisible()
        assert get_work_item(iid)['description'] == expected
        with read_session() as session:
            with Image.open(media_path(session.get(Capture, cid).image_rel_path)) as image:
                assert image.getpixel((60, 30))[0] > 200
    finally:
        dialog._close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
