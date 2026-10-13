import os
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def application():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_inline_project_reuses_settings_and_case_insensitive_name(app_env):
    app = application()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sqlalchemy import select
    from checkpoint.db import read_session
    from checkpoint.models import Project
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.settings_store import get_settings, update_settings
    pid = default_project_id()
    update_settings({'audio': {'mic_enabled': True}, 'hotkeys': {'capture': 'F7', 'review': ''}})
    panel = InboxPanel()
    panel.toggle(pid)
    panel.project_picker.setCurrentIndex(panel.project_picker.findData('__new__'))
    editor = panel.project_picker.lineEdit()
    assert not editor.text()
    QTest.keyClicks(editor, 'Fresh project')
    QTest.keyClick(editor, Qt.Key.Key_Return)
    app.processEvents()
    fresh = panel.project_id
    assert fresh != pid and panel.project_picker.currentText() == 'Fresh project'
    saved = get_settings()
    assert saved.last_project_id == fresh and saved.audio.mic_enabled and saved.hotkeys.capture == 'F7'
    assert not panel.project_picker.isEditable()
    panel.project_picker.setCurrentIndex(panel.project_picker.findData('__new__'))
    panel.project_picker.setEditText('FRESH PROJECT')
    panel._commit_project()
    assert panel.project_id == fresh
    with read_session() as session:
        assert len(list(session.scalars(select(Project).where(Project.name == 'Fresh project')))) == 1
    assert panel.deck.item is None
    assert not hasattr(panel, 'edit_button')
    assert not hasattr(panel, 'delete_button')
    panel.close()


def test_session_filter_preserves_selection_and_unrecorded_tasks(app_env):
    app = application()
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item
    from checkpoint.db import write_session
    from checkpoint.models import Session, WorkItem
    pid = default_project_id()
    unsessioned = create_work_item(pid, 'note', 'Without recording', 'Typed note')
    with write_session() as session:
        recording = Session(project_id=pid, title='Playtest', state='ended')
        session.add(recording)
        session.flush()
        sid = recording.id
        task = WorkItem(project_id=pid, session_id=sid, type='bug', title='Recorded', description='Speech')
        session.add(task)
        session.flush()
        iid = task.id
    panel = InboxPanel()
    panel.toggle(pid)
    panel.session_picker.setCurrentIndex(panel.session_picker.findData(sid))
    assert [item['id'] for item in panel.items] == [iid]
    panel.refresh()
    assert panel.selected_id == iid and panel.session_picker.currentData() == sid
    panel.session_picker.setCurrentIndex(panel.session_picker.findData('__unrecorded__'))
    assert [item['id'] for item in panel.items] == [unsessioned['id']]
    panel.close()


def test_images_button_opens_selected_project_status(app_env, monkeypatch):
    app = application()
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services import image_cards
    from checkpoint.services.capture import default_project_id
    from checkpoint.services.review import create_work_item
    from PySide6.QtGui import QDesktopServices
    pid = default_project_id()
    item = create_work_item(pid, 'bug', 'Current task', 'Fix this')
    panel = InboxPanel()
    panel.toggle(pid)
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    panel._images()
    assert Path(opened[-1]) == image_cards.output_folder(pid, 'open')
    panel._status('done')
    panel.status_picker.setCurrentIndex(panel.status_picker.findData('done'))
    panel._images()
    assert Path(opened[-1]) == image_cards.output_folder(pid, 'done')
    assert panel.images_button.text() == 'Open done images'
    panel._status('deleted')
    panel.status_picker.setCurrentIndex(panel.status_picker.findData('deleted'))
    panel._images()
    assert Path(opened[-1]) == image_cards.output_folder(pid, 'deleted')
    assert panel.selected_id == item['id']
    panel.close()


def test_loading_card_tracks_project_work_and_cannot_be_swiped(app_env):
    app = application()
    from PySide6.QtCore import QPointF
    from checkpoint.desktop.inbox import InboxPanel
    from checkpoint.services.capture import default_project_id
    from checkpoint.db import write_session
    from checkpoint.models import Project, Session, AudioSource, AudioChunk, TranscriptionJob
    pid = default_project_id()
    with write_session() as db:
        other = Project(name='Other')
        db.add(other)
        recording = Session(project_id=pid, title='Pending recording', state='ended')
        db.add(recording)
        db.flush()
        other_id, sid = other.id, recording.id
        source = AudioSource(session_id=sid, kind='mic')
        db.add(source)
        db.flush()
        chunk = AudioChunk(session_id=sid, source_id=source.id, seq=0, rel_path='test.wav',
                           start_offset_ms=0, duration_ms=1000, sample_rate=16000, channels=1, state='complete')
        db.add(chunk)
        db.flush()
        job = TranscriptionJob(session_id=sid, chunk_id=chunk.id, state='running')
        db.add(job)
        db.flush()
        jid = job.id
    panel = InboxPanel()
    panel.toggle(pid)
    assert panel.deck.processing_message == 'Adding transcript context…'
    assert panel.deck.spinner.isActive()
    assert panel.deck.item is None
    decided = []
    panel.deck.decide.connect(decided.append)
    center = panel.deck._loading_rect().center()
    panel.deck.begin_drag(center)
    panel.deck.end_drag(center - QPointF(0, 140))
    assert not decided
    assert panel.status_picker.width() == panel.session_picker.width()
    assert panel.status_picker.y() == panel.session_picker.y()
    assert abs(panel.session_picker.geometry().center().y() - panel.search.geometry().center().y()) <= 1
    assert panel.record_button.isVisible() and not hasattr(panel, 'activity_button')
    assert not hasattr(panel, 'new_session_button')
    available = app.primaryScreen().availableGeometry().adjusted(12, 12, -12, -12)
    assert panel.geometry() == available
    panel.session_picker.setCurrentIndex(panel.session_picker.findData('__unrecorded__'))
    assert not panel.deck.processing_message and not panel.deck.spinner.isActive()
    panel.session_picker.setCurrentIndex(0)
    panel.project_picker.setCurrentIndex(panel.project_picker.findData(other_id))
    assert not panel.deck.processing_message
    panel.project_picker.setCurrentIndex(panel.project_picker.findData(pid))
    assert panel.deck.processing_message
    with write_session() as db:
        db.get(TranscriptionJob, jid).state = 'done'
    panel.refresh()
    assert not panel.deck.processing_message and not panel.deck.spinner.isActive()
    panel.close()


def test_chronological_carousel_numbers_and_restart_position(app_env):
    from datetime import datetime, timezone, timedelta
    app = application()
    from checkpoint.db import write_session
    from checkpoint.models import WorkItem
    from checkpoint.services.capture import default_project_id
    from checkpoint.desktop.inbox import InboxPanel
    pid = default_project_id()
    with write_session() as db:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tasks = [WorkItem(project_id=pid, type='note', title=f'Card {i + 1}', description='',
                          human_updated_at=base + timedelta(seconds=i)) for i in range(54)]
        db.add_all(tasks)
        db.flush()
        ids = [task.id for task in tasks]
    panel = InboxPanel()
    panel.toggle(pid)
    assert [item['id'] for item in panel.items] == ids
    assert panel.selected_id == ids[-1] and panel.deck.ordinal == 54
    panel._step(-2)
    assert panel.selected_id == ids[51] and panel.deck.ordinal == 52
    assert panel.position.text() == '54 cards'
    assert any(relative < -2 for relative, _, _ in panel.deck.neighbors())
    assert max(relative for relative, _, _ in panel.deck.neighbors()) == 2
    panel.hide()
    restored = InboxPanel()
    restored.toggle(pid)
    assert restored.selected_id == ids[51] and restored.deck.ordinal == 52
    restored._step(-100)
    assert restored.selected_id == ids[0] and restored.deck.ordinal == 1
    restored._step(100)
    assert restored.selected_id == ids[-1] and restored.deck.ordinal == 54
    panel.close()
    restored.close()


def test_project_picker_switches_by_default_and_double_click_renames(app_env, monkeypatch):
    app = application()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from checkpoint.desktop import inbox
    from checkpoint.services.capture import default_project_id
    from checkpoint.db import read_session
    from checkpoint.models import Project
    pid = default_project_id()
    panel = inbox.InboxPanel()
    panel.toggle(pid)
    picker = panel.project_picker
    assert not picker.isEditable() and picker.height() >= 48
    old_name = picker.currentText()
    panel._commit_project()
    assert picker.currentText() == old_name
    requested = []
    monkeypatch.setattr(inbox, 'get_text', lambda *args: requested.append(args) or ('Renamed project', True))
    QTest.mouseClick(picker, Qt.MouseButton.LeftButton)
    assert picker.view().isVisible()
    assert not requested
    picker.hidePopup()
    QTest.mouseDClick(picker, Qt.MouseButton.LeftButton)
    assert len(requested) == 1
    assert picker.currentText() == 'Renamed project'
    assert panel.project_id == pid and not picker.isEditable()
    with read_session() as session:
        assert session.get(Project, pid).name == 'Renamed project'
    panel.close()
