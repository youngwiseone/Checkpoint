import os, sys
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['CHECKPOINT_DATA_DIR']=str(Path('output/ui-concepts/sample-data').resolve())
sys.path.insert(0,str(Path('backend').resolve()))
from PySide6.QtWidgets import QApplication
from checkpoint import db
db.migrate()
from checkpoint.services.capture import default_project_id
from checkpoint.services.review import create_work_item
pid=default_project_id()
create_work_item(pid,'improvement','Make processing easier to follow','Show what is being transcribed and how much audio remains. Keep the captured moment and its note together.')
from checkpoint.desktop.inbox import InboxPanel
from checkpoint.desktop.settings import SettingsDialog
from checkpoint.audio import devices
from checkpoint import integrations
devices.list_devices=lambda: {'mic': [], 'loopback': []}
integrations.status=lambda: []
app=QApplication([])
from PySide6.QtGui import QFont, QFontDatabase
QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QFont('Segoe UI', 10))
panel=InboxPanel(recording=lambda: None,recording_status=lambda: {'active':True,'project_id':pid,'sources':[{'label':'Microphone','state':'recording'}]})
panel.toggle(pid)
app.processEvents()
panel.grab().save('output/ui-concepts/updated-inbox.png')
panel.hide()
settings=SettingsDialog()
settings.show()
app.processEvents()
settings.grab().save('output/ui-concepts/updated-settings.png')
print('Captured native inbox and settings widgets from current code.')
