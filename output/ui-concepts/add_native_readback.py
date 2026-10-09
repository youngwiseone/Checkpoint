from pathlib import Path
p=Path('output/ui-concepts/native_blur_qa.py')
s=p.read_text(encoding='utf-8')
s=s.replace("QTimer.singleShot(180000,app.quit)", "def capture_native():\n    from checkpoint.capture.win32 import window_rect\n    import mss\n    from PIL import Image\n    rect=window_rect(int(panel.winId()))\n    with mss.mss() as capture:\n        shot=capture.grab(rect.as_mss())\n        Image.frombytes('RGB',shot.size,shot.rgb).save('output/ui-concepts/native-compositor-check.png')\n    print('Native compositor readback saved',flush=True)\nQTimer.singleShot(1200,capture_native)\nQTimer.singleShot(180000,app.quit)")
p.write_text(s,encoding='utf-8')
