"""Render the real task editor using isolated synthetic evidence."""
import os
import sys
import tempfile
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
temporary = tempfile.TemporaryDirectory(prefix='checkpoint-task-editor-preview-')
os.environ['CHECKPOINT_DATA_DIR'] = temporary.name
sys.path.insert(0, str(Path('backend').resolve()))

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from checkpoint import db
from checkpoint.desktop.editor import TaskDialog
from checkpoint.models import utcnow
from checkpoint.services.capture import CaptureService, default_project_id
from checkpoint.services.inbox import ensure_capture
from checkpoint.services.sessions import SessionManager
from checkpoint.services.task_evidence import bundle_previous

app = QApplication([])
for filename in ('segoeui.ttf', 'seguisb.ttf', 'segoeuib.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)
app.setFont(QFont('Segoe UI', 10))
db.migrate()
manager = SessionManager()
service = CaptureService(manager)
for index in range(2):
    source = QPixmap(960, 540)
    source.fill(QColor('#eef3fb'))
    painter = QPainter(source)
    painter.fillRect(0, 0, 960, 58, QColor('#24334d'))
    painter.setPen(QColor('white'))
    painter.setFont(QFont('Segoe UI', 18))
    painter.drawText(28, 39, 'Example project - screenshot ' + str(index + 1))
    painter.setPen(QColor('#27354b'))
    painter.drawText(38, 120, 'A captured issue, ready to describe')
    for row in range(5):
        painter.fillRect(38, 160 + row * 60, 884, 42, QColor('#ffffff'))
    painter.end()
    png, jpg = Path(temporary.name) / f'{index}.png', Path(temporary.name) / f'{index}.jpg'
    source.save(str(png))
    source.save(str(jpg))
    cid = service.import_image(png.read_bytes(), jpg.read_bytes(), 960, 540,
        project_id=default_project_id(), session_id=None, offset_ms=None,
        taken_at=utcnow(), status='marker')
    current = ensure_capture(cid)
    if index == 0:
        iid = current
        service.save_capture_note(cid, '\n\n'.join([
            'The note is the most important part of this task. Give it enough space to read and edit without constantly scrolling.',
            'Keep the screenshot available as a compact reference. Put the pencil and trash controls on the image and use left/right arrows to browse attachments.',
            'Keep Add to last task, Snip image, and Open menu together in one row.',
            'Make the note area taller and keep the entire edit window on the screen.'
        ]))
    else:
        bundle_previous(current)
dialog = TaskDialog(iid)
dialog.show()
app.processEvents()
dialog.grab().save('output/ui-concepts/task-editor-expanded-note.png')
print('Note height:', dialog.description.height(), 'preview height:', dialog.screenshot.height())
dialog.close()
manager.shutdown()
db.reset_engine()
temporary.cleanup()
