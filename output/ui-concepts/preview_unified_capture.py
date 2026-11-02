import io
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from checkpoint import config, db
from checkpoint.desktop.editor import TaskDialog
from checkpoint.services.capture import CaptureService, default_project_id
from checkpoint.services.sessions import SessionManager

application = QApplication.instance() or QApplication([])
for filename in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf"):
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + filename)
application.setFont(QFont("Segoe UI", 10))
with tempfile.TemporaryDirectory(prefix='checkpoint-editor-preview-') as tmp:
    config.override_paths(Path(tmp))
    db.reset_engine()
    db.migrate()
    service = CaptureService(SessionManager())
    pid = default_project_id()
    def image(title, second=False):
        source = Image.new('RGB', (1440, 900), '#f5f8fc')
        draw = ImageDraw.Draw(source)
        font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 24)
        big = ImageFont.truetype('C:/Windows/Fonts/segoeuib.ttf', 40)
        draw.rectangle((0, 0, 1440, 80), fill='#17365b')
        draw.text((36, 18), 'CHECKPOINT  /  Example project', fill='white', font=font)
        draw.text((48, 122), title, fill='#15375b', font=big)
        draw.rectangle((48, 200, 1390, 270), fill='white', outline='#cad7e6', width=2)
        draw.text((72, 220), 'Search project captures…', fill='#64758b', font=font)
        for index in range(3):
            x = 48 + index * 452
            draw.rounded_rectangle((x, 320, x + 424, 720), radius=20, fill='white', outline='#c9d7e9', width=2)
            draw.rounded_rectangle((x + 20, 344, x + 404, 540), radius=12, fill=('#dcebf8' if not second else '#fce9d7'))
            draw.text((x + 24, 572), ['Button overlaps text', 'Settings panel spacing', 'Add capture shortcut'][index], fill='#15375b', font=font)
            draw.text((x + 24, 622), 'Open  ·  Just now', fill='#64758b', font=font)
        stream = io.BytesIO(); source.save(stream, 'PNG')
        thumb = source.copy(); thumb.thumbnail((480, 270))
        thumb_stream = io.BytesIO(); thumb.save(thumb_stream, 'JPEG')
        return stream.getvalue(), thumb_stream.getvalue()
    png, thumb = image('Review captures')
    cid = service.import_image(png, thumb, 1440, 900, project_id=pid, session_id=None, offset_ms=None,
        taken_at=datetime.now(timezone.utc), status='marker')
    iid = service.save_capture_note(cid, 'Move the search controls closer to the task cards.\n\nThe top margin is too large on smaller screens. I have marked the area in red. Keep the title and search field aligned with the first card.')['work_item_id']
    png, thumb = image('Settings comparison', True)
    service.import_image(png, thumb, 1440, 900, project_id=pid, session_id=None, offset_ms=None,
        taken_at=datetime.now(timezone.utc), status='saved', trigger='snip', attach_to=iid)
    dialog = TaskDialog(iid)
    dialog.place_on_screen = lambda: None
    dialog.dictation_status.setText('Listening… Release F9 or click Stop recording when finished.')
    dialog.mic_btn.setText('Stop recording')
    dialog.show()
    folder = Path(__file__).resolve().parent
    for size, filename, tool in [((1460, 880), 'unified-capture-wide.png', 'pencil'),
                                 ((1024, 700), 'unified-capture-compact.png', 'pencil'),
                                 ((1024, 700), 'unified-capture-shape-compact.png', 'shape')]:
        dialog.resize(*size)
        workspace = dialog.images.currentWidget()
        workspace.set_tool(tool)
        application.processEvents()
        dialog.grab().save(str(folder / filename))
        print(filename, 'dialog', dialog.size().toTuple(), 'workspace', workspace.size().toTuple(),
            'workspaceMin', workspace.minimumSizeHint().toTuple(), 'sidebar', dialog.splitter.widget(1).size().toTuple(), flush=True)
    dialog._closing = True
    dialog.close()
    db.reset_engine()
