"""Native voice playback with a single play/stop control.

Windows plays through winsound; Linux through the sound server's own player
(paplay or pw-play, falling back to ALSA's aplay), so no media backend is needed.
"""
import io
import shutil
import subprocess
import sys
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
        self.player = None
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
            data = evidence_audio(self.queue.pop(0))
            with wave.open(io.BytesIO(data), 'rb') as wav:
                duration = wav.getnframes() / wav.getframerate()
            if self.path:
                self.path.unlink(missing_ok=True)
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as output:
                output.write(data)
                self.path = Path(output.name)
            self._play(self.path)
            self.button.setIcon(control_icon('stop'))
            self.button.setToolTip('Stop voice playback')
            self.timer.start(max(1, int(duration * 1000) + 100))
        except (ImportError, ValueError, OSError, EOFError, RuntimeError) as error:
            self.stop()
            self.on_error(str(error))

    def stop(self):
        self.timer.stop()
        self.queue = []
        self._silence()
        if self.path:
            self.path.unlink(missing_ok=True)
            self.path = None
        self.button.setIcon(control_icon('play'))
        self.button.setToolTip('Play voice note')

    def _play(self, path):
        if sys.platform == 'win32':
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
        self._silence()
        player = next((p for p in ('paplay', 'pw-play', 'aplay') if shutil.which(p)), None)
        if player is None:
            raise RuntimeError('No audio player found. Install PulseAudio or PipeWire utilities (paplay or pw-play).')
        self.player = subprocess.Popen([player, str(path)], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _silence(self):
        if sys.platform == 'win32':
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except (ImportError, RuntimeError):
                pass
        elif self.player is not None:
            if self.player.poll() is None:
                self.player.terminate()
                try:
                    self.player.wait(1)
                except subprocess.TimeoutExpired:
                    self.player.kill()
            self.player = None
