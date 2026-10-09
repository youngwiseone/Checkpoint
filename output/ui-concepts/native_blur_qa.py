import os,sys,io
from pathlib import Path
from datetime import datetime,timezone
os.environ['QT_QPA_PLATFORM']='windows'
os.environ['CHECKPOINT_DATA_DIR']=str(Path('output/ui-concepts/stack-preview-data').resolve())
sys.path.insert(0,str(Path('backend').resolve()))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont,QFontDatabase,QPixmap,QPainter,QLinearGradient,QColor
from checkpoint import db
from checkpoint.services.capture import default_project_id,CaptureService
from checkpoint.services.sessions import SessionManager
from checkpoint.services.review import create_work_item,update_work_item
from checkpoint.services.inbox import ensure_capture
from checkpoint.desktop.inbox import InboxPanel
from checkpoint.desktop.settings import SettingsDialog
from checkpoint.desktop.editor import TaskDialog
from checkpoint.desktop.glass import TextDialog
from checkpoint.transcription import worker
from checkpoint.audio import devices
from checkpoint import integrations
from PIL import Image
db.migrate()
pid=default_project_id()
for title,desc in [('Keep the context together','Screenshots and captured words are always available when you expand the card.'),('Make review feel effortless','Swipe vertically to browse, click to expand and edit.')]:
    create_work_item(pid,'idea',title,desc)
source=Image.open('C:/Users/GGPC/AppData/Local/Temp/codex-clipboard-db81d3cb-aca1-4d3c-8642-c66518da540c.png').convert('RGB')
png=io.BytesIO();source.save(png,'PNG')
thumb=io.BytesIO();source.save(thumb,'JPEG')
cid=CaptureService(SessionManager()).import_image(png.getvalue(),thumb.getvalue(),source.width,source.height,project_id=pid,session_id=None,offset_ms=None,taken_at=datetime.now(timezone.utc),status='marker')
wid=ensure_capture(cid)
update_work_item(wid,{'title':'Make this feel like glass','description':'The desktop should shine through. Keep review compact, swipe through the stack, and click a card to expand.'})
devices.list_devices=lambda:{'mic':[],'loopback':[]}
integrations.status=lambda:[]
worker.transcription_status=lambda sid=None:{'done':32,'running':1,'queued':15,'failed':0}
app=QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QFont('Segoe UI',10))
panel=InboxPanel(recording=lambda:None,recording_status=lambda:{'active':True,'offset_ms':252000,'project_id':pid,'sources':[{'label':'Microphone','state':'recording'}]})
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QTimer, Qt
class Pattern(QWidget):
    def paintEvent(self, event):
        p=QPainter(self)
        p.fillRect(self.rect(),QColor('#eeeeee'))
        p.setPen(QColor('#151515'))
        p.setFont(QFont('Segoe UI',18))
        for y in range(0,self.height(),40):
            p.drawText(10,y+25,'SHARP DESKTOP TEXT 12345  SHARP DESKTOP TEXT 12345  SHARP DESKTOP TEXT')
        p.end()
background=Pattern()
background.setWindowTitle('Checkpoint blur test backdrop')
background.resize(900,920)
from checkpoint.desktop.glass import center_on_screen
center_on_screen(background)
background.show()
app.processEvents()
panel.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
panel.setWindowTitle('Checkpoint Blur QA')
panel.toggle(pid)
panel.selected_id=wid
panel.refresh()
panel.resize(460,800)
app.processEvents()
print('Primary screen',app.primaryScreen().availableGeometry(),'deck',panel.deck.size())
print('Frost backdrop captured:',not panel.backdrop.isNull(),flush=True)
def capture_native():
    from checkpoint.capture.win32 import window_rect
    import mss
    from PIL import Image
    rect=window_rect(int(panel.winId()))
    with mss.mss() as capture:
        shot=capture.grab(rect.as_mss())
        Image.frombytes('RGB',shot.size,shot.rgb).save('output/ui-concepts/corrected-ui-only-frost.png')
    print('Native compositor readback saved',flush=True)
QTimer.singleShot(1200,capture_native)
QTimer.singleShot(45000,app.quit)
watch=QTimer()
watch.timeout.connect(lambda:app.quit() if Path('output/ui-concepts/stop-blur-qa').exists() else None)
watch.start(300)
app.exec()
