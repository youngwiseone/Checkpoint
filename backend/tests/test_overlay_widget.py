"""The card overlay: swipes and clicks drive the same actions as the keys."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def overlay(app_env):
    from PySide6.QtWidgets import QApplication

    from checkpoint.desktop.overlay import ReviewOverlay
    from checkpoint.services import review as rv
    from test_agent_flow import _project, _session

    app = QApplication.instance() or QApplication([])
    pid = _project()
    sid = _session(pid)
    ids = [rv.create_draft(pid, sid, "bug", t, "")["id"] for t in ("First problem here", "Second problem here", "Third problem here")]
    ov = ReviewOverlay(None)
    ov.model.open(sid)
    ov.render()
    ov.show()
    yield ov, ids
    ov.close()
    del app


def _swipe(ov, dy):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    c = ov._front_rect().center().toPoint()
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=c)
    QTest.mouseMove(ov, c + QPoint(0, dy // 2))
    QTest.mouseMove(ov, c + QPoint(0, dy))
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=c + QPoint(0, dy))
    ov.anim.setCurrentTime(ov.anim.duration())


def _state(did):
    from checkpoint.db import read_session
    from checkpoint.models import DraftItem

    with read_session() as s:
        return s.get(DraftItem, did).review_state


def test_swipe_up_approves_down_dismisses_short_drag_snaps_back(overlay):
    ov, ids = overlay
    _swipe(ov, -40)  # not far enough: nothing happens
    assert _state(ids[0]) == "pending" and ov.model.card["id"] == ids[0]
    _swipe(ov, -140)
    assert _state(ids[0]) == "approved" and ov.model.card["id"] == ids[1]
    _swipe(ov, 140)
    assert _state(ids[1]) == "dismissed" and ov.model.card["id"] == ids[2]


def test_click_edits_and_keys_match(overlay):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    ov, ids = overlay
    QTest.mouseClick(ov, Qt.MouseButton.LeftButton, pos=ov._front_rect().center().toPoint())
    assert ov.model.mode == "edit" and ov.editor.isVisible()
    QTest.keyClick(ov.editor, Qt.Key.Key_A)  # typing, not approving
    assert _state(ids[0]) == "pending"
    QTest.keyClick(ov.editor, Qt.Key.Key_Escape)  # hides, keeps the text
    assert not ov.isVisible() and ov.model.mode == "edit"
    ov.model.cancel_edit()
    ov.render()
    QTest.keyClick(ov, Qt.Key.Key_Up)
    assert _state(ids[0]) == "approved"
    QTest.keyClick(ov, Qt.Key.Key_U)
    assert _state(ids[0]) == "pending" and ov.model.card["id"] == ids[0]
