"""Carousel interaction regressions: browsing must never dismiss a capture."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def deck():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPixmap, QColor
    from checkpoint.desktop.cards import TaskDeck
    app = QApplication.instance() or QApplication([])
    widget = TaskDeck()
    widget.resize(740, 600)
    image = QPixmap(1200, 700)
    image.fill(QColor('#92abca'))
    item = {'id': 'center', 'title': 'Center capture', 'description': 'A note with useful context. ' * 30, 'evidence': []}
    neighbors = [(relative, {**item, 'id': str(relative)}, image) for relative in (-2, -1, 1, 2)]
    widget.present(item, image, 5, behind=neighbors)
    widget.show()
    app.processEvents()
    return app, widget


def test_sideways_drag_is_continuous_then_snaps_and_never_decides():
    from PySide6.QtCore import QPointF
    app, widget = deck()
    navigated, decisions = [], []
    widget.navigate.connect(navigated.append)
    widget.decide.connect(decisions.append)
    origin = widget.card_rect().center()
    widget.begin_drag(origin)
    widget.drag_to(origin + QPointF(-45, 8))
    assert widget.horizontal_offset == -45
    assert widget.offset == 0
    widget.drag_to(origin + QPointF(-190, -125))
    # Once locked sideways, later vertical movement cannot approve/delete.
    assert widget.offset == 0
    widget.end_drag(origin + QPointF(-190, -125))
    assert navigated == [1]
    assert decisions == []
    assert widget.horizontal_animation.endValue() == 0
    widget.close()


def test_vertical_axis_stays_locked_and_short_drags_cancel():
    from PySide6.QtCore import QPointF
    app, widget = deck()
    navigated, decisions = [], []
    widget.navigate.connect(navigated.append)
    widget.decide.connect(decisions.append)
    origin = widget.card_rect().center()
    widget.begin_drag(origin)
    widget.drag_to(origin + QPointF(2, -20))
    widget.end_drag(origin + QPointF(180, -110))
    assert decisions == ['done']
    assert navigated == []
    assert widget.horizontal_offset == 0
    widget.animation.stop()
    widget.offset = 0
    widget.begin_drag(origin)
    widget.end_drag(origin + QPointF(20, 2))
    assert navigated == []
    assert decisions == ['done']
    widget.close()


def test_click_exposed_neighbor_focuses_without_editing():
    from PySide6.QtCore import QPointF
    app, widget = deck()
    navigated, edits = [], []
    widget.navigate.connect(navigated.append)
    widget.expand.connect(lambda: edits.append(True))
    # The exposed right edge belongs to the next card.
    point = QPointF(widget.card_rect().right() + 30, widget.card_rect().center().y())
    widget.begin_drag(point)
    widget.end_drag(point)
    assert navigated == [1]
    assert edits == []
    widget.horizontal_animation.stop()
    widget.horizontal_offset = 0
    point = widget.card_rect().center()
    widget.begin_drag(point)
    widget.end_drag(point)
    assert edits == [True]
    widget.close()


def test_refresh_preserves_drag_and_selected_neighbor_animates_from_live_position():
    from PySide6.QtCore import QPointF
    app, widget = deck()
    origin = widget.card_rect().center()
    widget.begin_drag(origin)
    widget.drag_to(origin + QPointF(-100, 2))
    widget.present(widget.item, widget.image, 5, behind=widget.behind)
    assert widget.horizontal_offset == -100
    assert widget.axis == 'horizontal'
    spacing = widget.card_spacing()
    next_item = {**widget.item, 'id': '1'}
    widget.present(next_item, widget.image, 5, 1, behind=widget.behind)
    assert abs(widget.horizontal_animation.startValue() - (spacing - 100)) < .01
    widget.close()


def test_preview_crop_and_hover_do_not_change_source_and_empty_deck_has_no_card():
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QPixmap
    app, widget = deck()
    original_size = widget.image.size()
    source = widget.image_source_rect(widget.image, QRectF(0, 0, 300, 500))
    assert abs(source.width() / source.height() - .6) < .001
    assert widget.image.size() == original_size
    widget.pointer = None
    collapsed = widget.grab().toImage()
    widget.drag_to(widget.card_rect().center())
    expanded = widget.grab().toImage()
    # The expanded note covers pixels further up the image on hover.
    rect = widget.card_rect()
    point = QPointF(rect.center().x(), rect.bottom() - rect.height() * .4).toPoint()
    assert collapsed.pixelColor(point) != expanded.pixelColor(point)
    widget.present(None, QPixmap(), 0)
    assert widget.surface_path().isEmpty()
    assert widget.grab().toImage().pixelColor(widget.rect().center()).alpha() == 0
    widget.close()


def test_caption_edits_and_wide_horizontal_drag_can_skip_two_cards():
    from PySide6.QtCore import QPointF
    app, widget = deck()
    edits, navigated, decisions = [], [], []
    widget.expand.connect(lambda: edits.append(True))
    widget.navigate.connect(navigated.append)
    widget.decide.connect(decisions.append)
    caption = QPointF(widget.card_rect().center().x(), widget.card_rect().bottom() - 20)
    widget.begin_drag(caption)
    widget.end_drag(caption)
    assert edits == [True]
    origin = widget.card_rect().center()
    widget.begin_drag(origin)
    widget.end_drag(origin + QPointF(-widget.card_spacing() * 1.8, 3))
    assert navigated == [2]
    assert decisions == []
    widget.close()


def test_horizontal_trackpad_wheel_browses_without_decisions():
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    app, widget = deck()
    navigated, decisions = [], []
    widget.navigate.connect(navigated.append)
    widget.decide.connect(decisions.append)
    origin = widget.card_rect().center()
    event = QWheelEvent(origin, QPointF(widget.mapToGlobal(origin.toPoint())), QPoint(-75, 0), QPoint(),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
    app.sendEvent(widget, event)
    assert navigated == [1]
    assert decisions == []
    widget.close()

def test_endpoint_buttons_jump_without_editing_or_deciding():
    app, widget = deck()
    widget.present(widget.item, widget.image, 5, behind=widget.behind, ordinal=3)
    jumps, edits, decisions = [], [], []
    widget.jump.connect(jumps.append)
    widget.expand.connect(lambda: edits.append(True))
    widget.decide.connect(decisions.append)
    widget.newest_button.click()
    widget.oldest_button.click()
    assert jumps == [True, False]
    assert edits == decisions == []
    widget.close()
