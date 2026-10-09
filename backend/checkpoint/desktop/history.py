"""Browse and correct recorded transcripts without a browser."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout,
                              QLabel, QListWidget, QListWidgetItem, QPushButton)
from sqlalchemy import select

from ..db import read_session, write_session
from ..models import AudioSource, EvidenceLink, Session, TranscriptSegment, WorkItem
from .windows import popup_css
from . import colors
from .glass import GlassDialog, get_text


class HistoryDialog(GlassDialog):
    def __init__(self, project_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transcript history")
        self.resize(780, 600)
        self.setStyleSheet(popup_css(colors.current()))
        layout = self.content_layout()
        self.sessions = QComboBox()
        with read_session() as s:
            query = select(Session).where(Session.is_demo.is_(False)).order_by(Session.started_at.desc())
            if project_id:
                query = query.where(Session.project_id == project_id)
            for session in s.scalars(query):
                self.sessions.addItem(f"{session.started_at:%Y-%m-%d %H:%M} · {session.title}", session.id)
        layout.addWidget(self.sessions)
        self.lines = QListWidget()
        self.lines.setWordWrap(True)
        self.lines.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.lines, 1)
        self.message = QLabel("Select transcript lines to make a task, or double-click a line to correct it.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        actions = QHBoxLayout()
        for label, callback in (("Refresh", self.refresh), ("Create task from selection", self._task), ("Close", self.accept)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.sessions.currentIndexChanged.connect(self.refresh)
        self.lines.itemDoubleClicked.connect(self._correct)
        self.refresh()

    def refresh(self):
        self.lines.clear()
        sid = self.sessions.currentData()
        if not sid:
            self.message.setText("No recordings yet. Start recording from the review panel.")
            return
        with read_session() as s:
            for segment in s.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == sid).order_by(TranscriptSegment.start_ms)):
                source = s.get(AudioSource, segment.source_id)
                seconds = segment.start_ms // 1000
                item = QListWidgetItem(f"{seconds // 60:02}:{seconds % 60:02} · {source.label if source else 'Audio'}\n{segment.corrected_text or segment.text}")
                item.setData(Qt.ItemDataRole.UserRole, segment.id)
                self.lines.addItem(item)

    def _correct(self, item):
        segment_id = item.data(Qt.ItemDataRole.UserRole)
        with read_session() as s:
            segment = s.get(TranscriptSegment, segment_id)
            text = segment.corrected_text or segment.text
        text, ok = get_text(self, "Correct transcript", "Recognised words", text, multiline=True)
        if ok and text.strip():
            with write_session() as s:
                s.get(TranscriptSegment, segment_id).corrected_text = text.strip()
            self.refresh()

    def _task(self):
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.lines.selectedItems()]
        if not ids:
            self.message.setText("Select one or more lines first.")
            return
        with write_session() as s:
            segments = s.scalars(select(TranscriptSegment).where(TranscriptSegment.id.in_(ids)).order_by(TranscriptSegment.start_ms)).all()
            session = s.get(Session, self.sessions.currentData())
            text = "\n".join(segment.corrected_text or segment.text for segment in segments)
            work = WorkItem(project_id=session.project_id, session_id=session.id, type="task", title=text.splitlines()[0][:300], description=text)
            s.add(work)
            s.flush()
            for segment in segments:
                s.add(EvidenceLink(work_item_id=work.id, segment_id=segment.id, confidence="direct", attached_by="user"))
        self.message.setText("Task added to the inbox with the selected transcript evidence.")
