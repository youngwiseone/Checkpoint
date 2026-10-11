"""Reproducible native carousel previews with synthetic screenshot evidence."""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, 'backend')
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QImage, QFontDatabase
from checkpoint.desktop.inbox import InboxPanel
from checkpoint.desktop.backdrop import frosted_image
from checkpoint.desktop.windows import popup_css
from checkpoint.desktop import colors
app=QApplication([])
for font in ('segoeui.ttf','seguisb.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',28)

def pixmap(image):
    raw=image.tobytes()
    return QPixmap.fromImage(QImage(raw,image.width,image.height,image.width*3,QImage.Format.Format_RGB888).copy())

def screenshot(label,color):
    image=Image.new('RGB',(1200,800),'#eaf0f5'); d=ImageDraw.Draw(image)
    d.rectangle((0,0,1200,90),fill='#18283a'); d.text((60,30),label,font=font,fill='white')
    d.rounded_rectangle((330,145,880,740),radius=22,fill='white')
    d.text((380,185),'Your project overview',font=font,fill='#18283a')
    d.rounded_rectangle((380,250,820,480),radius=18,fill=color)
    d.text((410,290),'Capture a moment',font=font,fill='white')
    d.text((410,345),'Keep the context',font=font,fill='white')
    for y in (535,605,675):
        d.rounded_rectangle((380,y,820,y+38),radius=8,fill='#dce5ed')
    return pixmap(image)

item={'id':'center','title':'The toolbar covers the Save button','description':'The menu overlaps the Save button. Move it below the toolbar so the action stays visible. Keep the screenshot and note together when sharing this with Codex.','evidence':[]}
image=screenshot('CHECKPOINT / Dashboard','#426b8c')
backs=[(-1,{**item,'id':'previous','title':'Make settings easier to find'},screenshot('CHECKPOINT / Settings','#4f7566')),(1,{**item,'id':'next','title':'Keep screenshot notes readable'},screenshot('CHECKPOINT / Notes','#7b5c88'))]
for width,height in ((1600,900),):
    panel=InboxPanel(recording=lambda:None)
    panel.setStyleSheet(popup_css(colors.current()))
    panel.resize(width,height)
    panel.project_picker.blockSignals(True)
    panel.project_picker.addItem('Checkpoint project','preview')
    panel.position.setText('2 of 3')
    panel.deck.present(item,image,3,behind=backs)
    panel.deck.set_processing('Adding transcript context\u2026')
    panel.show(); app.processEvents()
    background=Image.new('RGB',(panel.width(),panel.height()),'#18283a'); d=ImageDraw.Draw(background)
    for i,color in enumerate(('#235447','#355782','#775e7d','#425467')):
        d.rectangle((i*panel.width()//4,0,(i+1)*panel.width()//4,panel.height()),fill=color)
    panel.backdrop=pixmap(frosted_image(background,background.size))
    panel.grab().save(f'output/ui-concepts/wide-inbox-{width}x{height}.png')
    panel.deck.pointer=panel.deck.card_rect().center()
    panel.grab().save(f'output/ui-concepts/wide-inbox-{width}x{height}-hover.png')
    print(f'{width}x{height}: actual {panel.width()}x{panel.height()}, card {panel.deck.card_rect().size()}')
    panel.close()
