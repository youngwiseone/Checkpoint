from pathlib import Path
s=Path('output/ui-concepts/render_stack.py').read_text(encoding='utf-8')
s=s.replace("os.environ['QT_QPA_PLATFORM']='offscreen'", "os.environ['QT_QPA_PLATFORM']='windows'")
s=s.replace('panel.toggle(pid)', '''from PySide6.QtWidgets import QWidget
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
panel.setWindowTitle('Checkpoint Blur QA')
panel.toggle(pid)''')
a=s.index('def save(widget,name):')
s=s[:a]+'''print('Native blur enabled:',panel.blur_enabled,flush=True)
QTimer.singleShot(180000,app.quit)
watch=QTimer()
watch.timeout.connect(lambda:app.quit() if Path('output/ui-concepts/stop-blur-qa').exists() else None)
watch.start(300)
app.exec()
'''
Path('output/ui-concepts/native_blur_qa.py').write_text(s,encoding='utf-8')
