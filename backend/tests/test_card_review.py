import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def application():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_review_opens_centered_every_time_and_keeps_transparent_space(app_env):
    app = application()
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item
    pid = default_project_id()
    create_work_item(pid, 'bug', 'Review card', 'Captured note')
    panel = InboxPanel()
    screen = app.primaryScreen().availableGeometry()
    work = {'left': screen.left(), 'top': screen.top(), 'width': screen.width(), 'height': screen.height()}
    for _ in range(2):
        panel.toggle(pid, work)
        app.processEvents()
        assert (panel.geometry().center() - screen.center()).manhattanLength() <= 2
        assert panel.deck.card_rect().width() <= 320
        image = panel.grab().toImage()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(5, image.height() // 2).alpha() == 0
        panel.hide()
        panel.move(5, 5)
    panel.close()


def test_swipe_wheel_keys_and_click_with_bounded_navigation(app_env, monkeypatch):
    app = application()
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item, get_work_item
    pid = default_project_id()
    for i in range(3):
        create_work_item(pid, 'bug', f'Card {i}', 'Details')
    panel = InboxPanel()
    panel.toggle(pid)
    app.processEvents()
    expanded = []
    monkeypatch.setattr(panel, '_open', lambda item_id: expanded.append(item_id))
    ids = [item['id'] for item in panel.items]
    deck = panel.deck
    origin = deck.card_rect().center().toPoint()
    QTest.mousePress(deck, Qt.MouseButton.LeftButton, pos=origin)
    QTest.mouseMove(deck, origin - QPoint(0, 90))
    QTest.mouseRelease(deck, Qt.MouseButton.LeftButton, pos=origin - QPoint(0, 90))
    assert panel.selected_id == ids[1]
    assert get_work_item(ids[-1])['work_status'] == 'done'
    assert expanded == []
    panel.refresh()  # Background polling must preserve the selected card.
    assert panel.selected_id == ids[1]
    QTest.keyClick(deck, Qt.Key.Key_Left)
    QTest.keyClick(deck, Qt.Key.Key_Left)
    assert panel.selected_id == ids[0]
    event = QWheelEvent(QPointF(origin), QPointF(deck.mapToGlobal(origin)), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
    app.sendEvent(deck, event)
    assert panel.selected_id == ids[1]
    deck.animation.stop()
    deck.offset = 0
    QTest.mouseClick(deck, Qt.MouseButton.LeftButton, pos=deck.card_rect().center().toPoint())
    assert expanded == [ids[1]]
    QTest.keyClick(deck, Qt.Key.Key_Up)
    QTest.keyClick(deck, Qt.Key.Key_Up)
    assert panel.selected_id == ids[0]
    panel.close()


def test_nested_dialogs_are_frameless_and_translucent(app_env, monkeypatch):
    app = application()
    from PySide6.QtCore import Qt
    from checkpoint.desktop.settings import SettingsDialog
    from checkpoint.desktop.editor import TaskDialog, ScreenshotDialog
    from checkpoint.desktop.history import HistoryDialog
    from checkpoint.desktop.glass import TextDialog
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.inbox import ensure_capture
    from checkpoint.audio import devices
    from checkpoint import integrations
    from test_simple_inbox import captured
    monkeypatch.setattr(devices, 'list_devices', lambda: {'mic': [], 'loopback': []})
    monkeypatch.setattr(integrations, 'status', lambda: [])
    _, cid, _ = captured(app_env)
    wid = ensure_capture(cid)
    settings = SettingsDialog()
    dialogs = [settings, TextDialog(settings, 'Add project', 'Project name'),
               TextDialog(settings, 'Add note', 'Note', multiline=True),
               TaskDialog(wid, settings), ScreenshotDialog(cid, settings),
               HistoryDialog(default_project_id(), settings)]
    for dialog in dialogs:
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dialog.show()
        app.processEvents()
        image = dialog.grab().toImage()
        assert image.pixelColor(0, 0).alpha() == 0
        # The actual shell tint transmits the backdrop rather than just looking dark.
        assert 180 < image.pixelColor(10, image.height() // 2).alpha() < 240
        dialog.close()


def test_text_and_directory_prompts_return_values_without_native_windows(app_env, monkeypatch, tmp_path):
    app = application()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QFileDialog, QLineEdit
    from checkpoint.desktop.glass import GlassDialog, TextDialog, get_directory, get_text

    def accept(dialog):
        dialog.show()
        app.processEvents()
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        if isinstance(dialog, TextDialog):
            dialog.field.setText('New project')
            dialog.accept()
        else:
            chooser = dialog.findChild(QFileDialog)
            assert chooser.testOption(QFileDialog.Option.DontUseNativeDialog)
            assert not chooser.isWindow()
            chooser.setDirectory(str(tmp_path.parent))
            app.processEvents()
            chooser.selectFile(str(tmp_path))
            app.processEvents()
            chooser.findChild(QLineEdit, 'fileNameEdit').setText(str(tmp_path))
            chooser.accept()
        return dialog.result()

    monkeypatch.setattr(GlassDialog, 'exec', accept)
    assert get_text(None, 'Add project', 'Project name') == ('New project', True)
    from pathlib import Path
    assert Path(get_directory(None, 'Data folder')) == tmp_path
    monkeypatch.setattr(GlassDialog, 'exec', lambda dialog: QDialog.DialogCode.Rejected)
    assert get_text(None, 'Rename project', 'Project name', 'Existing') == ('Existing', False)
    assert get_directory(None, 'Data folder') == ''


def test_swipe_delete_trash_undo_and_real_back_cards(app_env):
    app = application()
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item, get_work_item, list_work_items
    from checkpoint.services.inbox import reconcile
    pid = default_project_id()
    for i in range(3):
        create_work_item(pid, 'task', f'Real task {i}', f'Actual note {i}')
    panel = InboxPanel()
    panel.toggle(pid)
    app.processEvents()
    first = panel.selected_id
    assert len(panel.deck.behind) == 2
    assert all(item['id'] != first and item['title'].startswith('Real task') for _, item, _ in panel.deck.behind)
    origin = panel.deck.card_rect().center().toPoint()
    # A short drag previews deletion but does not mutate the task.
    QTest.mousePress(panel.deck, Qt.MouseButton.LeftButton, pos=origin)
    QTest.mouseMove(panel.deck, origin + QPoint(0, 35))
    QTest.mouseRelease(panel.deck, Qt.MouseButton.LeftButton, pos=origin + QPoint(0, 35))
    assert get_work_item(first)['work_status'] == 'open'
    panel.deck.animation.stop()
    panel.deck.offset = 0
    QTest.mousePress(panel.deck, Qt.MouseButton.LeftButton, pos=origin)
    QTest.mouseMove(panel.deck, origin + QPoint(0, 120))
    QTest.mouseRelease(panel.deck, Qt.MouseButton.LeftButton, pos=origin + QPoint(0, 120))
    assert get_work_item(first)['work_status'] == 'deleted'
    assert first not in {item['id'] for item in list_work_items(pid)}
    reconcile()
    assert get_work_item(first)['work_status'] == 'deleted'
    assert first in {item['id'] for item in list_work_items(pid, statuses=['deleted'])}
    panel._undo()
    assert get_work_item(first)['work_status'] == 'open'
    assert panel.selected_id == first
    panel.close()


def test_frost_blurs_pixels_only_inside_smooth_ui_shapes(app_env):
    app = application()
    from PIL import Image
    import numpy as np
    from PySide6.QtGui import QImage, QPixmap
    from checkpoint.desktop.backdrop import frosted_image
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item
    source = Image.new('RGB', (460, 800), 'white')
    for y in range(800):
        for x in range(460):
            if (x // 4 + y // 4) % 2:
                source.putpixel((x, y), (0, 0, 0))
    blurred = frosted_image(source, source.size)
    assert np.asarray(blurred).std() < np.asarray(source).std() / 5
    pid = default_project_id()
    create_work_item(pid, 'task', 'Front', '')
    panel = InboxPanel()
    panel.toggle(pid)
    app.processEvents()
    blurred = blurred.resize((panel.width(), panel.height()))
    data = blurred.tobytes()
    panel.backdrop = QPixmap.fromImage(QImage(data, blurred.width, blurred.height, blurred.width * 3, QImage.Format.Format_RGB888).copy())
    image = panel.grab().toImage()
    assert image.pixelColor(5, image.height() // 2).alpha() == 0
    assert panel.mask().isEmpty()  # No hard-edged OS region mask.
    assert any(0 < image.pixelColor(x, y).alpha() < 255 for x in range(10, 30) for y in range(10, 30))
    panel.close()
