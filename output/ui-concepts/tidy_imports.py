from pathlib import Path
for file in ['inbox.py','settings.py','editor.py','history.py','host.py']:
    p=Path('backend/checkpoint/desktop')/file
    s=p.read_text(encoding='utf-8')
    if file=='inbox.py':
        s=s.replace('QInputDialog, ', '').replace('QGridLayout, ', '').replace('QVBoxLayout, QWidget', 'QVBoxLayout')
        s=s.replace('for n, (label, callback) in enumerate((("Previous",', 'for label, callback in (("Previous",')
        s=s.replace('("Archive", lambda: self._status("wont_do")), ("Undo", self._undo))):', '("Archive", lambda: self._status("wont_do")), ("Undo", self._undo)):')
        s=s.replace('for n, (label, callback) in enumerate((("Add note",', 'for label, callback in (("Add note",')
        s=s.replace('("Settings", self._settings))):', '("Settings", self._settings)):')
    if file=='settings.py':
        s=s.replace('QComboBox, QDialog, QDialogButtonBox, QFileDialog,', 'QComboBox, QDialogButtonBox,')
        s=s.replace('QHBoxLayout, QInputDialog, QLabel,', 'QHBoxLayout, QLabel,')
        s=s.replace('QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget', 'QPushButton, QTabWidget, QWidget')
    if file=='editor.py':
        s=s.replace('QComboBox, QDialog, QDialogButtonBox,', 'QComboBox, QDialogButtonBox,')
        s=s.replace('QPushButton, QScrollArea, QVBoxLayout, QWidget', 'QPushButton')
    if file=='history.py':
        s=s.replace('QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QInputDialog,', 'QAbstractItemView, QComboBox, QHBoxLayout,')
        s=s.replace('QPushButton, QVBoxLayout', 'QPushButton')
    if file=='host.py':
        s=s.replace('QApplication, QMenu, QMessageBox, QSystemTrayIcon', 'QApplication, QMenu, QSystemTrayIcon')
    p.write_text(s, encoding='utf-8')
