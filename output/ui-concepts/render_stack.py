import os,sys,io
from pathlib import Path
from datetime import datetime,timezone
os.environ['QT_QPA_PLATFORM']='offscreen'
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
panel.toggle(pid)
panel.selected_id=wid
panel.refresh()
panel.resize(460,800)
app.processEvents()
print('Primary screen',app.primaryScreen().availableGeometry(),'deck',panel.deck.size())
def save(widget,name):
    widget.show();app.processEvents()
    # Render the real translucent widgets over a neutral desktop-like gradient,
    # making alpha visible in the review artifact. No user desktop is modified.
    pix=QPixmap(widget.size());pix.fill(QColor('#334c65'))
    painter=QPainter(pix)
    gradient=QLinearGradient(0,0,pix.width(),pix.height())
    gradient.setColorAt(0,QColor('#788fa4'));gradient.setColorAt(.5,QColor('#38546a'));gradient.setColorAt(1,QColor('#8a7293'))
    painter.fillRect(pix.rect(),gradient)
    from PySide6.QtCore import QPoint
    widget.render(painter, QPoint())
    painter.end();pix.save('output/ui-concepts/'+name)
    widget.grab().save('output/ui-concepts/transparent-'+name)
save(panel,'implemented-card-stack.png')
settings=SettingsDialog(parent=panel);save(settings,'implemented-glass-settings.png');settings.hide()
text=TextDialog(panel,'Add project','Project name','Checkpoint');save(text,'implemented-add-project.png');text.hide()
editor=TaskDialog(wid,panel);save(editor,'implemented-expanded-card.png');editor.hide()
panel.hide()
