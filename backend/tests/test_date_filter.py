"""Local last-edit ranges, inclusive end dates, and the custom calendar flow."""
import os
from datetime import date, datetime, timedelta, timezone

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.mark.parametrize('preset,today,expected', [
    ('all', date(2026, 10, 15), (None, None)),
    ('today', date(2026, 10, 15), (date(2026, 10, 15), date(2026, 10, 15))),
    ('yesterday', date(2026, 1, 1), (date(2025, 12, 31), date(2025, 12, 31))),
    ('week', date(2026, 10, 15), (date(2026, 10, 12), date(2026, 10, 18))),
    ('week', date(2026, 10, 12), (date(2026, 10, 12), date(2026, 10, 18))),
    ('week', date(2026, 10, 18), (date(2026, 10, 12), date(2026, 10, 18))),
    ('month', date(2024, 2, 15), (date(2024, 2, 1), date(2024, 2, 29))),
    ('month', date(2026, 12, 15), (date(2026, 12, 1), date(2026, 12, 31))),
])
def test_calendar_presets(preset, today, expected):
    from checkpoint.desktop.date_filter import date_range
    assert date_range(preset, today=today) == expected


def test_local_midnights_cover_full_end_day_including_dst_changes():
    from checkpoint.desktop.date_filter import utc_bounds
    for day in (date(2026, 4, 5), date(2026, 9, 27), date(2026, 10, 15)):
        start, before = utc_bounds(day, day)
        assert start.astimezone().date() == day
        assert start.astimezone().hour == 0
        assert before.astimezone().date() == day + timedelta(days=1)
        assert before.astimezone().hour == 0
        assert (before - timedelta(microseconds=1)).astimezone().date() == day
        assert before - start in (timedelta(hours=23), timedelta(hours=24), timedelta(hours=25))


def test_query_uses_human_edit_time_and_includes_local_end_date(app_env):
    from checkpoint.db import write_session
    from checkpoint.models import WorkItem
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import list_work_items
    pid = default_project_id()
    # NZ daylight time: the local 15th begins on the previous UTC date.
    nz = timezone(timedelta(hours=13))
    start = datetime(2026, 10, 15, tzinfo=nz)
    before = datetime(2026, 10, 17, tzinfo=nz)
    with write_session() as session:
        for title, edited in (
            ('Before start', start - timedelta(microseconds=1)),
            ('At start', start),
            ('Late on end date', before - timedelta(microseconds=1)),
            ('After end', before),
        ):
            session.add(WorkItem(project_id=pid, type='task', title=title,
                                 created_at=start - timedelta(days=50), human_updated_at=edited,
                                 updated_at=datetime.now(timezone.utc)))
    found = list_work_items(pid, modified_since=start, modified_before=before)
    assert {item['title'] for item in found} == {'At start', 'Late on end date'}
    assert len(list_work_items(pid)) == 4  # Callers without a date range stay unrestricted.
    assert [i['title'] for i in list_work_items(pid, q='Late', modified_since=start,
                                              modified_before=before)] == ['Late on end date']


def test_old_task_moves_to_today_after_edit_and_background_changes_do_not(app_env):
    from PySide6.QtWidgets import QApplication
    from checkpoint.db import write_session
    from checkpoint.models import WorkItem
    from checkpoint.desktop.date_filter import utc_bounds
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import update_work_item
    app = QApplication.instance() or QApplication([])
    pid = default_project_id()
    start, _ = utc_bounds(date.today(), date.today())
    with write_session() as session:
        task = WorkItem(project_id=pid, type='task', title='Older task',
                        created_at=start - timedelta(days=50), human_updated_at=start - timedelta(hours=1))
        session.add(task)
        session.flush()
        iid = task.id
    panel = InboxPanel()
    panel.toggle(pid)
    assert panel.date_picker.currentData() == 'all'
    assert panel.selected_id == iid
    panel.date_picker.setCurrentIndex(panel.date_picker.findData('today'))
    assert not panel.items
    with write_session() as session:
        session.get(WorkItem, iid).updated_at = datetime.now(timezone.utc)
    panel.refresh()
    assert not panel.items
    panel.date_picker.setCurrentIndex(panel.date_picker.findData('yesterday'))
    assert panel.selected_id == iid
    update_work_item(iid, {'description': 'Edited now'})
    panel.refresh()
    assert not panel.items
    panel.date_picker.setCurrentIndex(panel.date_picker.findData('today'))
    assert panel.selected_id == iid
    assert panel.deck.item['description'] == 'Edited now'
    panel.close()


def test_custom_apply_cancel_and_reopen_keep_the_applied_range(app_env, monkeypatch):
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication, QDialog
    from checkpoint.desktop.date_filter import DateRangeDialog
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    app = QApplication.instance() or QApplication([])
    panel = InboxPanel()
    panel.toggle(default_project_id())
    assert [panel.date_picker.itemText(n) for n in range(panel.date_picker.count())] == [
        'All', 'Today', 'Yesterday', 'This Week', 'This Month', 'Custom…']
    assert not hasattr(panel, 'session_picker')
    calls = []
    accept = True

    def choose(dialog):
        calls.append(dialog.dates())
        assert panel.isVisible()
        assert not panel.timer.isActive()
        dialog.start_calendar.setSelectedDate(QDate(2026, 1, 2))
        dialog.end_calendar.setSelectedDate(QDate(2026, 1, 5))
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(DateRangeDialog, 'exec', choose)
    panel.date_picker.setCurrentIndex(panel.date_picker.findData('week'))
    assert not calls
    panel.date_picker.setCurrentIndex(0)
    custom = panel.date_picker.findData('custom')
    panel.date_picker.setCurrentIndex(custom)
    panel.date_picker.activated.emit(custom)
    assert len(calls) == 1
    assert calls[0] == (date.today(), date.today())
    assert panel._date_range() == (date(2026, 1, 2), date(2026, 1, 5))
    key = panel._position_key()
    assert '2026-01-02:2026-01-05' in key
    assert panel.timer.isActive()
    panel.date_picker.activated.emit(custom)
    assert len(calls) == 2  # Selecting Custom again reopens the date controls.
    panel.date_picker.setCurrentIndex(panel.date_picker.findData('yesterday'))
    before = panel._date_range()
    accept = False
    panel.date_picker.setCurrentIndex(custom)
    panel.date_picker.activated.emit(custom)
    assert panel.date_picker.currentData() == 'yesterday'
    assert panel._date_range() == before
    assert panel.isVisible() and panel.timer.isActive()
    panel.close()


def test_custom_calendar_rejects_reversed_range_and_uses_glass_shell():
    from PySide6.QtCore import QDate, Qt
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    from checkpoint.desktop.date_filter import DateRangeDialog
    app = QApplication.instance() or QApplication([])
    dialog = DateRangeDialog(date(2026, 10, 15), date(2026, 10, 15))
    dialog.show()
    app.processEvents()
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dialog.start_calendar.setSelectedDate(QDate(2026, 10, 20))
    apply = dialog.buttons.button(QDialogButtonBox.StandardButton.Apply)
    assert not apply.isEnabled()
    dialog.end_calendar.setSelectedDate(QDate(2026, 10, 21))
    assert apply.isEnabled()
    assert dialog.dates() == (date(2026, 10, 20), date(2026, 10, 21))
    dialog.close()
