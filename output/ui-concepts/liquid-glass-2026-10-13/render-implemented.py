"""Render the real desktop widgets against a synthetic bright report, using temporary data."""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
temporary = tempfile.TemporaryDirectory(prefix='checkpoint-pearl-preview-')
os.environ['CHECKPOINT_DATA_DIR'] = temporary.name
sys.path.insert(0, str(Path('backend').resolve()))

from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeView
from checkpoint import db, integrations
from checkpoint.audio import devices
from checkpoint.desktop.backdrop import frosted_image
from checkpoint.desktop.editor import ScreenshotDialog
from checkpoint.desktop.glass import DirectoryDialog, TextDialog
from checkpoint.desktop.inbox import InboxPanel
from checkpoint.desktop.settings import SettingsDialog
from checkpoint.desktop.windows import NoteWindow
from checkpoint.models import Project
from checkpoint.services.capture import CaptureService, default_project_id
from checkpoint.services.inbox import ensure_capture
from checkpoint.services.review import update_work_item
from checkpoint.services.sessions import SessionManager

destination = Path(__file__).resolve().parent
app = QApplication([])
for filename in ('segoeui.ttf', 'seguisb.ttf', 'segoeuib.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)
app.setFont(QFont('Segoe UI', 10))
db.migrate()
pid = default_project_id()
with db.write_session() as session:
    session.get(Project, pid).name = 'Nees AWS'

# A portrait evidence fixture, not a screenshot of the user's desktop or data.
source = QPixmap(320, 540)
source.fill(QColor('#faf9f7'))
p = QPainter(source)
p.fillRect(0, 0, 320, 42, QColor('#292b2e'))
p.setFont(QFont('Segoe UI', 8))
for index, (label, value) in enumerate((('Customer', 'Nees AWS'), ('Job Ref #', '123456'),
                                       ('Created by', 'Customer Service Platform'),
                                       ('Delivery Address', '14 Market Street'),
                                       ('Suggested Delivery Vehicle', 'Van - Medium'),
                                       ('Delivery date', '13 October 2026'),
                                       ('Notes', 'Confirm delivery vehicle type'))):
    y = 64 + index * 58
    p.setPen(QColor('#4b5059'))
    p.drawText(18, y, label)
    p.setPen(QColor('#d4d4d4'))
    p.setBrush(QColor('white'))
    p.drawRoundedRect(QRect(18, y + 9, 284, 27), 3, 3)
    p.setPen(QColor('#303744'))
    p.drawText(26, y + 27, value)
p.end()
png, jpg = Path(temporary.name) / 'portrait.png', Path(temporary.name) / 'portrait.jpg'
source.save(str(png))
source.save(str(jpg))
manager = SessionManager()
cid = CaptureService(manager).import_image(png.read_bytes(), jpg.read_bytes(), 320, 540,
    project_id=pid, session_id=None, offset_ms=None, taken_at=datetime.now(timezone.utc), status='marker')
wid = ensure_capture(cid)
update_work_item(wid, {'title': 'Change Suggested Delivery Vehicle to Type', 'description': ''})
devices.list_devices = lambda: {'mic': [], 'loopback': []}
integrations.status = lambda: []

def report(width=1600, height=900):
    image = QPixmap(width, height)
    image.fill(QColor('#ffffff'))
    painter = QPainter(image)
    painter.fillRect(24, 18, width - 48, 74, QColor('#ef9843'))
    painter.setPen(QColor('white'))
    painter.setFont(QFont('Segoe UI', 22, QFont.Weight.DemiBold))
    painter.drawText(52, 65, 'Operations Report')
    painter.setPen(QColor('#b4b9c0'))
    painter.setFont(QFont('Segoe UI', 12))
    for y in range(150, height - 50, 58):
        painter.drawText(44, y, 'Delivery reference     Customer     Region     Status     Date')
        painter.drawLine(42, y + 17, width // 2, y + 17)
    for index in range(6):
        painter.fillRect(width - 375 + index * 48, 390 - index * 36, 30, 110 + index * 36, QColor('#78c5a0'))
    painter.end()
    return image

background = report()
pixels = background.toImage().convertToFormat(QImage.Format.Format_RGB888)
blurred = frosted_image(Image.frombytes('RGB', (pixels.width(), pixels.height()), bytes(pixels.constBits()),
                                        'raw', 'RGB', pixels.bytesPerLine()), (1600, 900))
data = blurred.tobytes()
frost = QPixmap.fromImage(QImage(data, 1600, 900, 1600 * 3, QImage.Format.Format_RGB888).copy())

def render(name, windows):
    canvas = QPixmap(background)
    painter = QPainter(canvas)
    for widget, x, y in windows:
        widget.backdrop = frost.copy(x, y, widget.width(), widget.height())
        widget.render(painter, QPoint(x, y))
    painter.end()
    path = destination / name
    assert canvas.save(str(path))
    print(path.name, flush=True)

panel = InboxPanel(recording=lambda: None, recording_status=lambda: {'active': True, 'project_id': pid, 'offset_ms': 12000})
panel.toggle(pid)
panel.resize(1600, 900)
app.processEvents()
panel.timer.stop()
panel.deck.present(panel.deck.item, source, 1, ordinal=1)
panel.deck.pointer = None
render('implemented-pearl-main.png', [(panel, 0, 0)])
panel.hide()

settings = SettingsDialog()
settings.show()
app.processEvents()
add = TextDialog(settings, 'Add project', 'Project name')
add.show()
app.processEvents()
render('implemented-pearl-settings.png', [(settings, 80, 130), (add, 880, 130)])
add.close()
settings.tabs.setCurrentIndex(4)
app.processEvents()
directory = DirectoryDialog(settings, 'Data folder for next launch')
fixture_folder = Path(temporary.name) / 'backend'
for name in ('checkpoint', 'server', 'tests'):
    (fixture_folder / name).mkdir(parents=True)
directory.chooser.setDirectory(str(fixture_folder))
directory.show()
tree = directory.chooser.findChild(QTreeView, 'treeView')
for _ in range(100):
    if tree.model().rowCount(tree.rootIndex()) == 3:
        break
    QTest.qWait(50)
assert tree.model().rowCount(tree.rootIndex()) == 3
print('Folder list: 3 folders loaded', flush=True)
render('implemented-pearl-storage.png', [(settings, 55, 75), (directory, 730, 265)])
directory.close()
settings.close()

note = NoteWindow(lambda *args: None, lambda *args: None, lambda *args: None)
note.open_for({'id': cid, 'thumb_path': str(jpg)})
note.text.setPlainText('Change the suggested delivery vehicle label to show the type.')
editor = ScreenshotDialog(cid)
editor.show()
app.processEvents()
render('implemented-pearl-note-editor.png', [(note, 40, 36), (editor, 560, 80)])
print('Note window size:', note.size(), 'content minimum:', note.minimumSizeHint(), flush=True)
editor.close()
note.hide()
manager.shutdown()
db.reset_engine()
temporary.cleanup()
