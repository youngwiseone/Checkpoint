"""The approved glass UI must remain readable and operate the real recording state."""
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def application():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_existing_install_gets_pearl_glass_without_losing_preferences(app_env):
    from checkpoint.db import write_session
    from checkpoint.models import Setting
    from checkpoint.settings_store import get_settings, SETTINGS_VERSION
    with write_session() as session:
        session.add(Setting(key='app', value={'version': 5, 'appearance': {'theme': 'dark'},
                                            'audio': {'mic_enabled': True, 'mic_device': 'My microphone'},
                                            'hotkeys': {'capture': 'F7'}, 'last_project_id': 'remembered'}))
    settings = get_settings()
    assert settings.version == SETTINGS_VERSION
    assert settings.appearance.theme == 'light'
    assert settings.audio.mic_enabled and settings.audio.mic_device == 'My microphone'
    assert settings.hotkeys.capture == 'F7' and settings.last_project_id == 'remembered'


def test_record_button_starts_stops_and_tracks_the_real_session(app_env):
    app = application()
    from checkpoint.desktop.host import DesktopHost
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.sessions import SessionManager
    from checkpoint.settings_store import update_settings
    pid = default_project_id()
    update_settings({'last_project_id': pid})
    manager = SessionManager()
    host = DesktopHost.__new__(DesktopHost)
    host.core = SimpleNamespace(sessions=manager, transcriber=Mock(), inbox=Mock())
    host.tray, host._refresh_tray = Mock(), Mock()
    panel = InboxPanel(recording=host._record_from_panel, recording_status=manager.status)
    panel.toggle(pid)
    app.processEvents()
    try:
        assert not panel.record_button.isChecked()
        panel.record_button.click()
        assert manager.active is not None and manager.active.project_id == pid
        assert panel.record_button.isChecked() and panel.record_button.property('recording')
        assert panel.record_button.accessibleName() == 'Stop recording'
        assert not panel.project_picker.isEnabled()
        panel.record_button.click()
        assert manager.active is None
        assert not panel.record_button.isChecked() and not panel.record_button.property('recording')
        assert panel.record_button.accessibleName() == 'Start recording'
        assert panel.project_picker.isEnabled()
        host.core.transcriber.wake.assert_called_once()
    finally:
        manager.shutdown()
        panel.close()


def test_failed_recording_does_not_leave_a_false_red_state(app_env):
    app = application()
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    panel = InboxPanel(recording=Mock(side_effect=ValueError('Microphone unavailable')),
                       recording_status=lambda: {'active': False})
    panel.toggle(default_project_id())
    app.processEvents()
    panel.record_button.click()
    assert 'Microphone unavailable' in panel.message.text()
    assert not panel.record_button.isChecked()
    assert not panel.record_button.property('recording')
    panel.close()


def test_folder_picker_populates_real_folders_and_has_readable_sidebar(app_env, tmp_path):
    app = application()
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QListView, QTreeView
    from checkpoint.desktop.glass import DirectoryDialog
    expected = {'checkpoint', 'server', 'tests'}
    folder = tmp_path / 'folders'
    for name in expected:
        (folder / name).mkdir(parents=True)
    dialog = DirectoryDialog(None, 'Data folder for next launch')
    dialog.chooser.setDirectory(str(folder))
    dialog.show()
    tree = dialog.chooser.findChild(QTreeView, 'treeView')
    model = tree.model()
    for _ in range(100):
        app.processEvents()
        if model.rowCount(tree.rootIndex()) == len(expected):
            break
        QTest.qWait(50)
    names = {model.index(row, 0, tree.rootIndex()).data() for row in range(model.rowCount(tree.rootIndex()))}
    assert names == expected
    assert dialog.chooser.findChild(QListView, 'sidebar').width() >= 150
    dialog.close()


@pytest.mark.parametrize('width', [640, 1280])
def test_toolbar_fits_and_keeps_record_next_to_settings(app_env, width):
    app = application()
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    panel = InboxPanel()
    panel.toggle(default_project_id())
    panel.resize(width, 800)
    app.processEvents()
    controls = [panel.project_picker, panel.search, panel.status_picker, panel.date_picker,
                panel.settings_button, panel.record_button, panel.close_button]
    for control in controls:
        assert panel.rect().contains(control.geometry())
    for index, control in enumerate(controls):
        assert all(not control.geometry().intersects(other.geometry()) for other in controls[index + 1:])
    assert panel.settings_button.x() < panel.record_button.x() < panel.close_button.x()
    if width >= 1000:
        centers = [control.geometry().center().y() for control in controls]
        assert max(centers) - min(centers) <= 1
        assert panel.project_picker.x() < panel.search.x() < panel.status_picker.x()
    panel.close()


@pytest.mark.parametrize('background', ['white', 'black'])
def test_card_caption_is_readable_without_hover_and_pencil_edits(app_env, background):
    app = application()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtTest import QTest
    from checkpoint.desktop import colors
    from checkpoint.desktop.cards import TaskDeck
    from checkpoint.desktop.windows import popup_css
    widget = TaskDeck()
    widget.setStyleSheet(popup_css(colors.current()))
    widget.resize(800, 660)
    source = QPixmap(320, 540)
    source.fill(QColor(background))
    widget.present({'id': 'card', 'title': 'Older task title',
                    'description': 'Change Suggested Delivery Vehicle to Type', 'evidence': []}, source, 1, ordinal=1)
    widget.show()
    app.processEvents()
    widget.pointer = None
    card, caption = widget.card_rect(), widget.focused_title_rect()
    assert card.contains(caption)
    assert .58 <= card.width() / card.height() <= .61
    image = widget.grab().toImage()
    assert image.pixelColor(int(caption.center().x()), int(caption.bottom() - 8)).lightness() > 210
    text_area = caption.adjusted(16, 16, -16, -16).toRect()
    assert any(image.pixelColor(x, y).lightness() < 65
               for x in range(text_area.left(), text_area.right(), 2)
               for y in range(text_area.top(), text_area.bottom(), 2))
    assert source.size() == widget.image.size()
    assert source.toImage().pixelColor(160, 530) == QColor(background)
    assert card.contains(widget.edit_button.geometry())
    edits, decisions = [], []
    widget.expand.connect(lambda: edits.append(True))
    widget.decide.connect(decisions.append)
    QTest.mouseClick(widget.edit_button, Qt.MouseButton.LeftButton)
    assert edits == [True] and decisions == []
    widget.close()


@pytest.mark.parametrize('size', [(780, 820), (640, 720)])
def test_task_editor_keeps_note_readable_and_actions_on_one_row(app_env, size):
    app = application()
    from PySide6.QtWidgets import QPushButton
    from checkpoint.desktop.editor import TaskDialog
    from test_simple_inbox import captured
    service, cid, _ = captured(app_env)
    note = '\n'.join(f'Line {index}: describe the change clearly.' for index in range(1, 13))
    iid = service.save_capture_note(cid, note)['work_item_id']
    dialog = TaskDialog(iid)
    dialog.resize(*size)
    dialog.show()
    app.processEvents()
    try:
        assert dialog.description.height() >= 240
        assert dialog.description.height() > dialog.screenshot.height()
        assert dialog.description.toPlainText() == note
        assert dialog.rect().contains(dialog.description.geometry())
        assert not dialog.words.isVisible()
        controls = [button for button in dialog.findChildren(QPushButton)
                    if button.text() in ('Add to last task', 'Snip image', 'Open menu', 'Captured words')]
        assert len(controls) == 4
        assert len({button.geometry().center().y() for button in controls}) == 1
        assert all(dialog.rect().contains(button.geometry()) for button in controls)
        assert dialog.screenshot.rect().contains(dialog.edit_image_btn.geometry())
        assert dialog.screenshot.rect().contains(dialog.remove_image_btn.geometry())
        assert dialog.edit_image_btn.x() < dialog.remove_image_btn.x()
        assert not dialog.previous_image_btn.isVisible()
        assert not dialog.next_image_btn.isVisible()
        dialog.words_button.click()
        app.processEvents()
        assert dialog.words.isVisible() and note in dialog.words.toPlainText()
        assert dialog.description.height() >= 240
        assert dialog.size().height() <= size[1]
    finally:
        dialog.close()


def test_task_editor_image_arrows_edit_and_remove_the_displayed_image(app_env, monkeypatch):
    app = application()
    from checkpoint.desktop import editor
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.services.review import get_work_item
    from checkpoint.services.task_evidence import bundle_previous
    from test_simple_inbox import captured
    service, first, _ = captured(app_env)
    iid = service.save_capture_note(first, 'First note')['work_item_id']
    _, second, _ = captured(app_env)
    bundle_previous(ensure_capture(second))
    dialog = editor.TaskDialog(iid)
    dialog.show()
    app.processEvents()
    try:
        assert not dialog.previous_image_btn.isEnabled()
        dialog.next_image_btn.click()
        selected = dialog._current_capture_id()
        assert selected != dialog.captures[0]['capture_id']
        assert not dialog.next_image_btn.isEnabled()
        assert dialog.screenshot.counter.text() == '2 / 2'
        for button in (dialog.previous_image_btn, dialog.next_image_btn, dialog.edit_image_btn, dialog.remove_image_btn):
            assert dialog.screenshot.rect().contains(button.geometry())
        screenshot_editor = Mock()
        monkeypatch.setattr(editor, 'ScreenshotDialog', screenshot_editor)
        dialog.description.setPlainText('Updated note stays intact')
        dialog.edit_image_btn.click()
        screenshot_editor.assert_called_once_with(selected, dialog)
        assert dialog._current_capture_id() == selected
        dialog.remove_image_btn.click()
        assert len(dialog.captures) == 1
        assert dialog._current_capture_id() != selected
        assert not dialog.previous_image_btn.isVisible()
        assert not dialog.next_image_btn.isVisible()
        assert get_work_item(iid)['description'] == 'Updated note stays intact'
        dialog.remove_image_btn.click()
        assert not dialog.screenshot.isVisible()
        assert not dialog.edit_image_btn.isEnabled()
        assert dialog.description.toPlainText() == 'Updated note stays intact'
    finally:
        dialog.close()
