from pathlib import Path
p=Path('output/ui-concepts/native_blur_qa.py')
s=p.read_text(encoding='utf-8').replace("print('Native blur enabled:',panel.blur_enabled,flush=True)", "print('Frost backdrop captured:',not panel.backdrop.isNull(),flush=True)").replace('native-compositor-check.png','corrected-ui-only-frost.png').replace('QTimer.singleShot(180000,app.quit)','QTimer.singleShot(45000,app.quit)')
p.write_text(s,encoding='utf-8')
