import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, 'backend')
from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtGui import QPixmap, QImage, QPainter, QFontDatabase
from checkpoint.desktop.cards import TaskDeck
from checkpoint.desktop.backdrop import frosted_image, paint_backdrop
from checkpoint.desktop import colors
app=QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
QFontDatabase.addApplicationFont('C:/Windows/Fonts/seguisb.ttf')
class Preview(QWidget):
    switcher_backdrop=True
    def paintEvent(self, event):
        p=QPainter(self); paint_backdrop(p,self); p.end()
panel=Preview(); panel.resize(460,570)
layout=QVBoxLayout(panel); layout.setContentsMargins(10,10,10,10)
deck=TaskDeck(panel); panel.deck=deck; layout.addWidget(deck)
source=Image.new('RGB',(460,570),'#172435'); d=ImageDraw.Draw(source)
for i,c in enumerate(['#236957','#365184','#627569','#79698a']): d.rectangle((i*120,40,i*120+180,500),fill=c)
source=frosted_image(source,source.size); data=source.tobytes()
panel.backdrop=QPixmap.fromImage(QImage(data,460,570,460*3,QImage.Format.Format_RGB888).copy())
screen=Image.new('RGB',(960,540),'#f2f5f8'); d=ImageDraw.Draw(screen)
d.rectangle((0,0,960,60),fill='#132435'); d.text((35,24),'CHECKPOINT / Project dashboard',fill='white')
d.rounded_rectangle((40,105,595,465),radius=16,fill='white',outline='#ced4dc',width=2)
d.text((70,140),'Capture inbox',fill='#172435'); d.rectangle((70,190,550,245),fill='#dde7ed')
d.rectangle((70,275,550,330),fill='#d9ece5'); d.rectangle((70,360,550,415),fill='#e8e1f2')
d.rounded_rectangle((640,105,910,465),radius=16,fill='#ffffff',outline='#ced4dc',width=2)
data=screen.tobytes(); image=QPixmap.fromImage(QImage(data,960,540,960*3,QImage.Format.Format_RGB888).copy())
item={'id':'1','title':'Screenshot notes stay together','type':'bug','work_status':'open','description':'The menu covers the Save button. Move it below the toolbar so the action stays visible.','evidence':[]}
backs=[({**item,'id':'2','title':'Settings spacing'},image),({**item,'id':'3','title':'A quick typed note'},QPixmap())]
deck.present(item,image,3,behind=backs)
panel.show(); app.processEvents(); panel.grab().save('output/ui-concepts/app-switcher-cards.png')


from checkpoint.desktop.inbox import InboxPanel
from checkpoint.desktop.windows import popup_css
for width,height in ((460,800),(360,600)):
    inbox=InboxPanel()
    inbox.setStyleSheet(popup_css(colors.current()))
    inbox.resize(width,height)
    inbox.project_picker.blockSignals(True)
    inbox.project_picker.addItem('Checkpoint project', 'preview')
    inbox.position.setText('Task 1 of 3')
    inbox.audio_status.setText('Audio recording is off')
    inbox.processing_label.setText('No audio clips to process')
    inbox.processing_progress.hide()
    inbox.deck.present(item,image,3,behind=backs)
    inbox.show(); app.processEvents()
    bg=source.resize((inbox.width(),inbox.height())); raw=bg.tobytes()
    inbox.backdrop=QPixmap.fromImage(QImage(raw,bg.width,bg.height,bg.width*3,QImage.Format.Format_RGB888).copy())
    inbox.grab().save(f'output/ui-concepts/inbox-{width}x{height}.png')
    print(f'inbox {width}x{height}: actual={inbox.width()}x{inbox.height()}, card={inbox.deck.card_rect().size()}')
    inbox.close()
