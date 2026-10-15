"""Preview the native last-modified dropdown and custom calendars with no user data."""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['CHECKPOINT_DATA_DIR'] = tempfile.mkdtemp(prefix='checkpoint-dates-preview-')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from checkpoint.desktop.date_filter import DateRangeDialog
from checkpoint.desktop.inbox import InboxPanel
from checkpoint.desktop.styles import popup_css
from checkpoint.desktop import colors

app = QApplication([])
for filename in ('segoeui.ttf', 'seguisb.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)
destination = ROOT / 'output' / 'ui-concepts'
dialog = DateRangeDialog(date(2026, 10, 12), date(2026, 10, 15))
dialog.show()
app.processEvents()
dialog.grab().save(str(destination / 'custom-date-filter.png'))
dialog.close()
panel = InboxPanel()
panel.setStyleSheet(popup_css(colors.current()))
panel.show()
app.processEvents()
panel.date_picker.showPopup()
app.processEvents()
panel.date_picker.view().window().grab().save(str(destination / 'date-filter-presets.png'))
panel.date_picker.hidePopup()
panel.close()
print('Saved custom calendar and preset dropdown previews.')
