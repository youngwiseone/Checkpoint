"""Render the actual Qt card effects with synthetic task content, without user data."""
import os
import sys
import tempfile
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['CHECKPOINT_DATA_DIR'] = tempfile.mkdtemp(prefix='checkpoint-effects-preview-')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from checkpoint.desktop.cards import TaskDeck
from checkpoint.desktop.windows import popup_css
from checkpoint.desktop import colors


def pixmap(image):
    raw = image.tobytes()
    return QPixmap.fromImage(QImage(raw, image.width, image.height, image.width * 3,
                                  QImage.Format.Format_RGB888).copy())


def pil(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes('RGBA', (image.width(), image.height()), bytes(image.constBits()),
                           'raw', 'RGBA', image.bytesPerLine())


app = QApplication([])
for filename in ('segoeui.ttf', 'seguisb.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)
font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 20)
source = Image.new('RGB', (600, 900), '#eff3f9')
draw = ImageDraw.Draw(source)
draw.rounded_rectangle((25, 30, 575, 100), radius=18, fill='#e0e7f1')
draw.text((50, 50), 'Checkpoint / Project notes', font=font, fill='#263c58')
for n, label in enumerate(('Keep the existing design', 'Add a clear completion tick', 'Make swipes feel satisfying')):
    y = 145 + n * 185
    draw.rounded_rectangle((35, y, 565, y + 155), radius=20, fill='white')
    draw.ellipse((60, y + 30, 120, y + 90), fill=('#45b97d', '#5d9af0', '#e5a24d')[n])
    draw.text((145, y + 45), label, font=font, fill='#263c58')
    draw.rounded_rectangle((65, y + 110, 470, y + 120), radius=5, fill='#e0e7f1')

decks = []
item = {'id': 'preview', 'title': 'Finish the task', 'work_status': 'open',
        'description': 'Keep the frosted cards. Add a little celebration when this task is done.', 'evidence': []}
for status in ('done', 'deleted'):
    deck = TaskDeck()
    deck.setStyleSheet(popup_css(colors.current()))
    deck.resize(540, 560)
    deck.present(item, pixmap(source), 1)
    deck.show()
    app.processEvents()
    deck.pointer = None
    deck.animate_decision(status, {'preview'})
    deck.effects.timer.stop()
    decks.append(deck)

frames = []
for n in range(33):
    elapsed = n * .05
    canvas = Image.new('RGB', (1080, 625), '#eaf0f6')
    draw = ImageDraw.Draw(canvas)
    draw.text((30, 20), 'Swipe up / Done', font=font, fill='#21874c')
    draw.text((570, 20), 'Swipe down / Delete', font=font, fill='#cc492d')
    for i, deck in enumerate(decks):
        frame = QPixmap(deck.size())
        frame.fill(Qt.GlobalColor.transparent)
        painter = QPainter(frame)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        deck.effects.paint(painter, elapsed)
        painter.end()
        layer = pil(frame.toImage())
        canvas.paste(layer, (i * 540, 55), layer)
    frames.append(canvas)

destination = ROOT / 'output' / 'ui-concepts'
frames[9].save(destination / 'card-decision-effects.png')
frames[0].save(destination / 'card-decision-effects.gif', save_all=True, append_images=frames[1:],
               duration=[50] * 32 + [650], loop=0, optimize=True)

# A separate static preview shows the persistent completion ticks on the carousel.
deck = decks[0]
deck.effects.clear()
deck.resize(1100, 620)
done = {**item, 'work_status': 'done'}
neighbors = [(n, {**done, 'id': f'neighbor-{n}'}, pixmap(source)) for n in (-1, 1)]
deck.present(done, pixmap(source), 3, behind=neighbors, ordinal=2)
app.processEvents()
rendered = pil(deck.grab().toImage())
background = Image.new('RGB', rendered.size, '#eaf0f6')
background.paste(rendered, (0, 0), rendered)
background.save(destination / 'completed-card-ticks.png')
for deck in decks:
    deck.close()
print('Saved native effect GIF, effect still, and completed carousel preview.')
