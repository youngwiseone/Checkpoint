"""Local calendar ranges for the task's last human edit timestamp."""
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QCalendarWidget, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout

from .glass import GlassDialog


DATE_PRESETS = (('All', 'all'), ('Today', 'today'), ('Yesterday', 'yesterday'), ('This Week', 'week'),
                ('This Month', 'month'), ('Custom…', 'custom'))


def date_range(preset, custom=None, today=None):
    if preset == 'all':
        return None, None
    today = today or date.today()
    if preset == 'yesterday':
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    if preset == 'week':
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if preset == 'month':
        return today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])
    if preset == 'custom' and custom:
        return custom
    return today, today


def utc_bounds(start, end):
    """Convert each local midnight separately, including the whole end date and DST."""
    return (datetime.combine(start, time.min).astimezone(timezone.utc) if start else None,
            datetime.combine(end + timedelta(days=1), time.min).astimezone(timezone.utc) if end else None)


class DateRangeDialog(GlassDialog):
    def __init__(self, start, end, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Last modified date')
        self.resize(780, 500)
        layout = self.content_layout()
        hint = QLabel('Show tasks last edited within this range. Both dates are included.')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        calendars = QHBoxLayout()
        calendars.setSpacing(18)
        self.start_calendar = QCalendarWidget()
        self.end_calendar = QCalendarWidget()
        for label, calendar, selected in (('Start date', self.start_calendar, start),
                                           ('End date', self.end_calendar, end)):
            column = QVBoxLayout()
            title = QLabel(label)
            column.addWidget(title)
            calendar.setAccessibleName(label)
            calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
            calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
            calendar.setSelectedDate(QDate(selected.year, selected.month, selected.day))
            column.addWidget(calendar)
            calendars.addLayout(column, 1)
        layout.addLayout(calendars)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setObjectName('primary')
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.start_calendar.selectionChanged.connect(self._update_range)
        self.end_calendar.selectionChanged.connect(self._update_range)
        self._update_range()

    def dates(self):
        return self.start_calendar.selectedDate().toPython(), self.end_calendar.selectedDate().toPython()

    def _update_range(self):
        start, end = self.dates()
        valid = start <= end
        self.summary.setText(f'{start:%d %b %Y} – {end:%d %b %Y}' if valid
                             else 'Choose an end date on or after the start date.')
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(valid)
