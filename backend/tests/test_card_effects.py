"""Decision feedback must survive refresh, leave no ghost cards, and respect Undo."""
import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def effect_frame(deck, elapsed):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter, QPixmap
    frame = QPixmap(deck.size())
    frame.fill(Qt.GlobalColor.transparent)
    painter = QPainter(frame)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    deck.effects.paint(painter, elapsed)
    painter.end()
    return frame.toImage()


def pixel_at(image, x, y):
    scale = image.devicePixelRatio()
    return image.pixelColor(int(x * scale), int(y * scale))


def test_completion_seals_show_on_both_exposed_neighbors_and_do_not_mark_archived():
    from test_card_carousel import deck
    app, widget = deck()
    item = {**widget.item, 'work_status': 'done'}
    neighbors = [(n, {**other, 'work_status': 'done'}, image) for n, other, image in widget.neighbors()]
    widget.present(item, widget.image, 5, behind=neighbors)
    image = widget.grab().toImage()
    points = []
    front = widget.card_rect()
    points.append((int(front.left() + 21), int(front.top() + 29)))
    for n in (-1, 1):
        rect = widget._neighbor_rect(n)
        points.append((int(max(0, rect.left()) + 21 if n < 0 else min(widget.width(), rect.right()) - 37),
                       int(rect.top() + 29)))
    for x, y in points:
        inks = [pixel_at(image, px, py) for px in range(x - 6, x + 6) for py in range(y - 10, y + 10)]
        assert sum(ink.green() > ink.red() + 25 and ink.green() > ink.blue() + 25 for ink in inks) > 30
    assert widget.accessibleDescription().startswith('Completed. ')
    widget.present({**item, 'work_status': 'wont_do'}, widget.image, 5, behind=neighbors)
    archived = pixel_at(widget.grab().toImage(), *points[0])
    assert archived != pixel_at(image, *points[0])
    widget.close()


@pytest.mark.parametrize('status,dy', [('done', -65), ('deleted', 65)])
def test_short_swipe_shows_decision_icon_without_starting_effect(status, dy):
    from PySide6.QtCore import QPointF
    from test_card_carousel import deck
    app, widget = deck()
    decisions = []
    widget.decide.connect(decisions.append)
    origin = widget.card_rect().center()
    widget.begin_drag(origin)
    widget.drag_to(origin + QPointF(0, dy))
    rect = widget.card_rect()
    image = widget.grab().toImage()
    inks = [pixel_at(image, x, y) for x in range(int(rect.center().x() - 20), int(rect.center().x() + 20))
            for y in range(int(rect.top() + 20), int(rect.top() + 68))]
    if status == 'done':
        assert any(ink.green() > ink.red() + 30 for ink in inks)
    else:
        assert any(ink.red() > ink.blue() + 40 for ink in inks)
    assert not widget.effects.cards
    widget.end_drag(origin + QPointF(0, dy))
    assert not decisions
    assert not widget.effects.timer.isActive()
    widget.close()


@pytest.mark.parametrize('status', ['done', 'deleted'])
def test_last_card_effect_survives_empty_refresh_and_undo_clears_it(app_env, status):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item, get_work_item
    app = QApplication.instance() or QApplication([])
    pid = default_project_id()
    item = create_work_item(pid, 'task', 'Last card', 'Keep this text legible under the falling confetti.')
    panel = InboxPanel()
    panel.toggle(pid)
    app.processEvents()
    deck = panel.deck
    origin = deck.card_rect().center()
    deck.begin_drag(origin)
    deck.end_drag(origin + QPointF(0, -110 if status == 'done' else 110))
    assert get_work_item(item['id'])['work_status'] == status
    assert deck.item is None
    assert deck.effects.timer.isActive()
    snapshot = deck.effects.cards[0][0]
    center = snapshot.rect.center().toPoint()
    assert effect_frame(deck, 0).pixelColor(center).alpha() > 0
    deck.effects.started = time.monotonic() - .4
    # The widget's paint path must still draw effects with an empty deck.
    rendered = deck.grab().toImage()
    assert any(pixel_at(rendered, snapshot.rect.left() + 8, y).alpha()
               for y in range(int(snapshot.rect.top() + 10), int(snapshot.rect.bottom() - 10), 20))
    if status == 'deleted':
        burned = effect_frame(deck, .4)
        assert burned.pixelColor(center).alpha() == 0
        assert burned.pixelColor(int(snapshot.rect.left() + 30), int(snapshot.rect.top() + 30)).alpha() > 0
    finished = effect_frame(deck, deck.effects.duration)
    assert finished.pixelColor(center).alpha() == 0
    panel._undo()
    assert get_work_item(item['id'])['work_status'] == 'open'
    assert deck.item['id'] == item['id']
    assert not deck.effects.cards
    assert not deck.effects.timer.isActive()
    panel.close()


def test_failed_batch_starts_no_effect_and_success_animates_only_changed_cards(app_env):
    from PySide6.QtCore import QRectF, Qt
    from test_batch_review import panel_with_cards, settle
    from checkpoint.services.review import update_work_item
    app, panel, ids = panel_with_cards()
    panel._select_cards(2, Qt.KeyboardModifier.ShiftModifier)
    settle(panel.deck)
    update_work_item(ids[1], {'title': 'Edited elsewhere'})
    panel._status('deleted')
    assert not panel.deck.effects.cards
    assert panel.undo_action is None
    panel.refresh()
    entries = [(rect, item) for rect, _, item, _ in panel.deck.back_cards()]
    entries.append((panel.deck.card_rect(), panel.deck.item))
    visible = {item['id'] for rect, item in entries
               if item['id'] in panel.selection and rect.intersects(QRectF(panel.deck.rect()))}
    panel._status('deleted')
    assert visible
    assert {snapshot.item_id for snapshot, _, _ in panel.deck.effects.cards} == visible
    panel.deck.effects.started = time.monotonic() - 2
    panel.deck.effects._tick()
    assert not panel.deck.effects.cards
    assert not panel.deck.effects.timer.isActive()
    panel.close()
