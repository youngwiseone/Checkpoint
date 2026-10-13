"""Modifier selection, atomic group decisions, and main-menu focus behavior."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def panel_with_cards():
    from PySide6.QtWidgets import QApplication
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item
    app = QApplication.instance() or QApplication([])
    pid = default_project_id()
    for n in range(6):
        create_work_item(pid, 'task', f'Card {n}', f'Note {n}')
    panel = InboxPanel()
    panel.toggle(pid)
    panel.resize(1400, 900)
    app.processEvents()
    panel._jump(False)
    return app, panel, [item['id'] for item in panel.items]


def settle(deck):
    deck.animation.stop()
    deck.horizontal_animation.stop()
    deck.offset = deck.horizontal_offset = 0
    deck._place_buttons()


@pytest.mark.parametrize('direction,status', [(-110, 'done'), (110, 'deleted')])
def test_shift_range_ctrl_exclusion_and_group_drag_with_undo(app_env, direction, status):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from checkpoint.services.review import get_work_item
    app, panel, ids = panel_with_cards()
    deck = panel.deck
    edits = []
    deck.expand.connect(lambda: edits.append(True))
    for _ in range(3):
        QTest.keyClick(deck, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        settle(deck)
    assert panel.selection == set(ids[:4])
    point = QPoint(int(deck.card_rect().left() - 30), int(deck.card_rect().center().y()))
    QTest.mouseClick(deck, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=point)
    settle(deck)
    assert panel.selected_id == ids[2]
    assert panel.selection == {ids[0], ids[1], ids[3]}
    panel.refresh()
    assert panel.selection == {ids[0], ids[1], ids[3]}
    # The focused card was deselected; drag the exposed selected neighbor instead.
    point = QPoint(int(deck.card_rect().right() + 30), int(deck.card_rect().center().y()))
    QTest.mousePress(deck, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(deck, point + QPoint(0, direction))
    QTest.mouseRelease(deck, Qt.MouseButton.LeftButton, pos=point + QPoint(0, direction))
    assert {get_work_item(item_id)['work_status'] for item_id in (ids[0], ids[1], ids[3])} == {status}
    assert all(get_work_item(item_id)['work_status'] == 'open' for item_id in (ids[2], ids[4], ids[5]))
    assert edits == []
    assert '3 cards' in panel.message.text()
    panel._undo()
    assert all(get_work_item(item_id)['work_status'] == 'open' for item_id in ids)
    assert panel.selection == {ids[0], ids[1], ids[3]}
    panel.close()


def test_shift_click_and_ctrl_toggle_do_not_open_editor(app_env):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    app, panel, ids = panel_with_cards()
    deck = panel.deck
    edits = []
    deck.expand.connect(lambda: edits.append(True))
    point = QPoint(int(deck.card_rect().right() + 30), int(deck.card_rect().center().y()))
    QTest.mouseClick(deck, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, pos=point)
    settle(deck)
    assert panel.selection == set(ids[:2])
    QTest.mouseClick(deck, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                     pos=deck.card_rect().center().toPoint())
    settle(deck)
    assert panel.selection == {ids[0]}
    assert edits == []
    panel.close()


def test_dragging_unselected_neighbor_only_changes_that_card(app_env):
    from PySide6.QtCore import QPointF, Qt
    from checkpoint.services.review import get_work_item
    app, panel, ids = panel_with_cards()
    panel._select_cards(2, Qt.KeyboardModifier.ShiftModifier)
    settle(panel.deck)
    # This neighbor is outside the selected group. Dragging it starts a new selection.
    origin = QPointF(panel.deck.card_rect().right() + 30, panel.deck.card_rect().center().y())
    panel.deck.begin_drag(origin)
    panel.deck.end_drag(origin + QPointF(0, -110))
    assert get_work_item(ids[3])['work_status'] == 'done'
    assert all(get_work_item(item_id)['work_status'] == 'open' for item_id in ids if item_id != ids[3])
    panel._undo()
    assert get_work_item(ids[3])['work_status'] == 'open'
    assert panel.selected_id == ids[3]
    panel.close()


def test_batch_and_undo_conflicts_never_partially_change_cards(app_env):
    from PySide6.QtCore import Qt
    from checkpoint.services.review import get_work_item, update_work_item
    app, panel, ids = panel_with_cards()
    panel._select_cards(2, Qt.KeyboardModifier.ShiftModifier)
    update_work_item(ids[1], {'title': 'Changed in another window'})
    panel._status('done')
    assert all(get_work_item(item_id)['work_status'] == 'open' for item_id in ids)
    assert 'changed elsewhere' in panel.message.text()
    panel.refresh()
    panel._status('done')
    update_work_item(ids[1], {'description': 'Changed again'})
    panel._undo()
    assert all(get_work_item(item_id)['work_status'] == 'done' for item_id in ids[:3])
    assert all(get_work_item(item_id)['work_status'] == 'open' for item_id in ids[3:])
    assert 'changed elsewhere' in panel.message.text()
    panel.close()


def test_short_or_horizontal_group_drag_and_deselection_are_safe(app_env):
    from PySide6.QtCore import QPointF, Qt
    from checkpoint.services.review import get_work_item
    app, panel, ids = panel_with_cards()
    panel._select_cards(2, Qt.KeyboardModifier.ShiftModifier)
    settle(panel.deck)
    for delta in (QPointF(2, -40), QPointF(-170, -120)):
        origin = panel.deck.card_rect().center()
        panel.deck.begin_drag(origin)
        panel.deck.end_drag(origin + delta)
        settle(panel.deck)
    assert panel.selection == set(ids[:3])
    assert all(get_work_item(item_id)['work_status'] == 'open' for item_id in ids)
    for item_id in list(panel.selection):
        current = ids.index(panel.selected_id)
        panel._select_cards(ids.index(item_id) - current, Qt.KeyboardModifier.ControlModifier)
    panel.refresh()
    assert not panel.selection
    panel._status('deleted')
    assert all(get_work_item(item_id)['work_status'] == 'open' for item_id in ids)
    panel.close()


def test_filter_never_leaves_hidden_cards_in_a_batch(app_env):
    from PySide6.QtCore import Qt
    from checkpoint.services.review import get_work_item
    app, panel, ids = panel_with_cards()
    panel._select_cards(5, Qt.KeyboardModifier.ShiftModifier)
    panel.search.setText('Card 2')
    assert panel.selection == {ids[2]}
    panel._status('deleted')
    assert get_work_item(ids[2])['work_status'] == 'deleted'
    assert all(get_work_item(item_id)['work_status'] == 'open' for item_id in ids if item_id != ids[2])
    panel.close()


def test_shift_endpoint_button_selects_the_entire_range(app_env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, panel, ids = panel_with_cards()
    QTest.mouseClick(panel.deck.newest_button, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    assert panel.selection == set(ids)
    panel.close()


def test_main_menu_hides_when_focus_moves_to_another_window(app_env):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget
    app, panel, ids = panel_with_cards()
    external = QWidget()
    external.show()
    external.activateWindow()
    QTest.qWait(130)
    assert not panel.isVisible()
    external.close()
    panel.close()


def test_main_menu_hides_when_another_application_is_active(app_env, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    app, panel, ids = panel_with_cards()
    # Qt may retain the last active app window while another application has focus.
    monkeypatch.setattr(QApplication, 'applicationState', staticmethod(lambda: Qt.ApplicationState.ApplicationInactive))
    panel._application_state_changed(Qt.ApplicationState.ApplicationInactive)
    QTest.qWait(130)
    assert not panel.isVisible()
    panel.close()


def test_dropdown_and_screenshot_editor_are_not_hidden_on_focus_loss(app_env):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget
    from checkpoint.desktop.editor import ScreenshotDialog
    from test_simple_inbox import captured
    app, panel, ids = panel_with_cards()
    panel.project_picker.showPopup()
    QTest.qWait(130)
    assert panel.isVisible()
    panel.project_picker.hidePopup()
    _, cid, _ = captured(app_env)
    editor = ScreenshotDialog(cid, panel)
    editor.show()
    external = QWidget()
    external.show()
    external.activateWindow()
    QTest.qWait(130)
    assert panel.isVisible() and editor.isVisible()
    editor.close()
    external.close()
    panel.close()


@pytest.mark.parametrize('action', ['_images', '_transcripts_folder'])
def test_folder_opens_after_main_menu_is_hidden(app_env, monkeypatch, action):
    from PySide6.QtGui import QDesktopServices
    app, panel, ids = panel_with_cards()
    opened = []
    def open_url(url):
        assert not panel.isVisible()
        opened.append(url.toLocalFile())
        return True
    monkeypatch.setattr(QDesktopServices, 'openUrl', open_url)
    getattr(panel, action)()
    assert opened
    panel.close()
