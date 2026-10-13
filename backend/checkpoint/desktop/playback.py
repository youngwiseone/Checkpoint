"""Native Windows voice playback with a single play/stop control."""
import io
import tempfile
import wave
from pathlib import Path

from PySide6.QtCore import QTimer

from ..services.voice import evidence_audio, playable_evidence
from .icons import control_icon


class VoicePlayback:
    def __init__(self, button, on_error):
        self.button, self.on_error = button, on_error
        self.timer = QTimer(button, singleShot=True, timeout=self._next)
        self.path = None
        self.queue = []
        self.entries = []
        self.button.clicked.connect(self.toggle)

    def set_evidence(self, evidence):
        self.entries = playable_evidence(evidence)
        self.button.setVisible(bool(self.entries))

    def toggle(self):
        if self.timer.isActive():
            self.stop()
            return
        self.queue = list(self.entries)
        self._next()

    def _next(self):
        if not self.queue:
            self.stop()
            return
        try:
            import winsound
            data = evidence_audio(self.queue.pop(0))
            with wave.open(io.BytesIO(data), 'rb') as wav:
                duration = wav.getnframes() / wav.getframerate()
            if self.path:
                self.path.unlink(missing_ok=True)
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as output:
                output.write(data)
                self.path = Path(output.name)
            winsound.PlaySound(str(self.path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.button.setIcon(control_icon('stop'))
            self.button.setToolTip('Stop voice playback')
            self.timer.start(max(1, int(duration * 1000) + 100))
        except (ImportError, ValueError, OSError, EOFError, RuntimeError) as error:
            self.stop()
            self.on_error(str(error))

    def stop(self):
        self.timer.stop()
        self.queue = []
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except (ImportError, RuntimeError):
            pass
        if self.path:
            self.path.unlink(missing_ok=True)
            self.path = None
        self.button.setIcon(control_icon('play'))
        self.button.setToolTip('Play voice note')
