from pathlib import Path
p=Path('backend/checkpoint/desktop/inbox.py')
s=p.read_text()
s=s.replace('from PySide6.QtGui import QPixmap, QPainter, QLinearGradient, QPen', 'from PySide6.QtGui import QPixmap')
s=s.replace('QScrollArea, QSizeGrip,', 'QGridLayout,')
s=s.replace('from .windows import popup_css', 'from .windows import popup_css\nfrom .glass import center_on_screen, get_text\nfrom .cards import TaskDeck')
s=s.replace('self.resize(760, 720)', 'self.resize(460, 830)\n        self.setMinimumSize(360, 600)\n        self._direction = 0')
s=s.replace('layout.setContentsMargins(24, 22, 24, 22)', 'layout.setContentsMargins(10, 10, 10, 10)')
s=s.replace('layout.setSpacing(14)', 'layout.setSpacing(8)')
s=s.replace('header = QHBoxLayout()', 'header_pill = QFrame()\n        header_pill.setObjectName("controlPill")\n        header = QHBoxLayout(header_pill)\n        header.setContentsMargins(14, 8, 10, 8)',1)
s=s.replace('font-size: 26px;', 'font-size: 20px;')
s=s.replace('layout.addLayout(header)', 'layout.addWidget(header_pill)',1)
s=s.replace('self.scroll = QScrollArea()\n        self.scroll.setWidgetResizable(True)\n        layout.addWidget(self.scroll)\n        actions = QHBoxLayout()', 'self.deck = TaskDeck(self)\n        self.deck.navigate.connect(self._step)\n        self.deck.expand.connect(lambda: self._open(self.selected_id))\n        layout.addWidget(self.deck, 1)\n        actions = QGridLayout()')
s=s.replace('for label, callback in (("Previous",', 'for n, (label, callback) in enumerate((("Previous",',1)
s=s.replace('("Archive", lambda: self._status("wont_do")), ("Undo", self._undo)):', '("Archive", lambda: self._status("wont_do")), ("Undo", self._undo))):',1)
s=s.replace('actions.addWidget(button)', 'actions.addWidget(button, n // 3, n % 3)',1)
s=s.replace('activity_layout.setContentsMargins(16, 14, 16, 14)', 'activity_layout.setContentsMargins(14, 10, 14, 10)\n        activity_layout.setSpacing(5)')
s=s.replace('bottom = QHBoxLayout()\n        for label, callback in (("Add note", self._add_note), ("Transcript history", self._history),\n                                ("Settings", self._settings)):', 'bottom = QGridLayout()\n        for n, (label, callback) in enumerate((("Add note", self._add_note), ("History", self._history),\n                                ("Settings", self._settings))):')
s=s.replace('bottom.addWidget(button)', 'bottom.addWidget(button, n // 2, n % 2)',1)
s=s.replace('bottom.addWidget(self.record_button)', 'bottom.addWidget(self.record_button, 1, 1)')
s=s.replace('        grip = QSizeGrip(self)\n        layout.addWidget(grip, 0, Qt.AlignmentFlag.AlignRight)\n','')
s=s.replace('text, ok = QInputDialog.getMultiLineText(self, "Add note", "What should we remember?")', 'text, ok = get_text(self, "Add note", "What should we remember?", multiline=True)')
start=s.index('        if work:\n',s.index('    def toggle('))
end=s.index('        self.raise_()',start)
s=s[:start]+'''        self.resize(460, 830)
        self.show()
        center_on_screen(self, work)
        self.deck.setFocus()
'''+s[end:]
start=s.index('        scroll_position =',s.index('    def refresh('))
end=s.index('        previous_ids =',start)
s=s[:start]+s[end:]
start=s.index('        if not ids:\n',s.index('    def refresh('))
end=s.index('\n    def _refresh_processing',start)
s=s[:start]+'''        item = get_work_item(self.selected_id) if ids else None
        pixmap = QPixmap()
        if item:
            self.done_button.setText("Reopen" if item["work_status"] in ("done", "wont_do") else "Done")
            captures = [e for e in item["evidence"] if e["kind"] == "capture"]
            if captures:
                self.frame_index = min(self.frame_index, len(captures) - 1)
                with read_session() as session:
                    capture = session.get(Capture, captures[self.frame_index]["capture_id"])
                    path = media_path(capture.image_rel_path) if capture else None
                if path:
                    pixmap = QPixmap(str(path))
        self.deck.present(item, pixmap, len(ids), self._direction)
        self._direction = 0
        self.message.setVisible(bool(self.message.text()))

'''+s[end:]
start=s.index('    def paintEvent(')
end=s.index('    def _done(',start)
s=s[:start]+s[end:]
s=s.replace('        self.selected_id = self.items[max(0, min(len(self.items) - 1, index + delta))]["id"]', '        self._direction = delta\n        self.selected_id = self.items[max(0, min(len(self.items) - 1, index + delta))]["id"]')
s=s.replace('        else:\n            super().keyPressEvent(event)', '        elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):\n            self._step(1 if event.key() == Qt.Key.Key_Down else -1)\n        else:\n            super().keyPressEvent(event)')
p.write_text(s)
