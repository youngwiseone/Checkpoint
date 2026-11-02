"""Copy complete task PNGs as images and pasteable files."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QMimeData, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ..settings_store import get_settings


def task_image_files(item_ids: list[str]) -> list[Path]:
    """Keep a snapshot so later edits or status changes cannot invalidate a copy."""
    from ..services.image_cards import write_folder

    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise ValueError('Select at least one task to copy.')
    folder = write_folder(ids)
    files = [folder / f'{item_id}-01-001.png' for item_id in ids]
    if not all(path.is_file() for path in files):
        raise ValueError('A selected task is no longer available. Refresh and try again.')
    return files


def copy_image_files(files: list[Path]) -> None:
    """Set the clipboard on the GUI thread, after every selected PNG is ready."""
    if not files or not all(path.is_file() for path in files):
        raise ValueError('The task images are no longer available. Copy them again.')
    mime = QMimeData()
    if len(files) == 1:
        png = files[0].read_bytes()
        image = QImage.fromData(png, 'PNG')
        if image.isNull():
            raise ValueError('The task image could not be read. Copy it again.')
        mime.setData('image/png', png)
        mime.setImageData(image)
    # Windows exposes local URLs as a file list, allowing all selected PNGs to
    # be pasted together. A single task also supports direct image pasting.
    mime.setUrls([QUrl.fromLocalFile(str(path.resolve())) for path in files])
    QApplication.clipboard().setMimeData(mime)


def latest_task_id() -> str | None:
    """Creation order keeps a late transcript or an older edit from taking over."""
    from sqlalchemy import select
    from ..db import read_session
    from ..models import Project, WorkItem

    with read_session() as session:
        item = session.scalars(select(WorkItem).join(Project).where(
            WorkItem.origin == 'local',
            Project.is_demo.is_(False), Project.archived.is_(False)
        ).order_by(WorkItem.created_at.desc(), WorkItem.id.desc()).limit(1)).first()
        seen = set()
        while item and item.duplicate_of_id and item.id not in seen:
            seen.add(item.id)
            item = session.get(WorkItem, item.duplicate_of_id)
        return item.id if item and item.work_status in ('open', 'in_progress', 'done') else None


class AutomaticTaskClipboard(QObject):
    """Render off-thread, publish on Qt, and discard superseded clipboard work."""

    ready = Signal(int, object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='task-clipboard')
        self._pending = set()
        self._revision = 0
        self._running = self._writing = self._stopped = False
        self._timer = QTimer(self, singleShot=True, interval=80, timeout=self._start)
        self.ready.connect(self._complete)
        QApplication.clipboard().dataChanged.connect(self._clipboard_changed)

    def request(self, item_id):
        if self._stopped or not get_settings().capture.auto_copy_latest:
            return
        self._pending.add(item_id)
        self._revision += 1
        self._timer.start()

    def _start(self):
        if self._stopped or self._running or not self._pending:
            return
        if not get_settings().capture.auto_copy_latest:
            self._pending.clear()
            return
        self._running = True
        self._workers.submit(self._prepare, self._revision, set(self._pending))

    def _prepare(self, revision, ids):
        item_id, files, error = None, None, ''
        try:
            item_id = latest_task_id()
            if item_id in ids:
                files = task_image_files([item_id])
        except Exception as exc:  # worker reports failures without touching the clipboard
            error = str(exc)
        self.ready.emit(revision, item_id, files, error)

    def _complete(self, revision, item_id, files, error):
        from ..events import hooks

        self._running = False
        if self._stopped:
            return
        if not get_settings().capture.auto_copy_latest:
            self._pending.clear()
            return
        if revision != self._revision:
            self._timer.start()
            return
        self._pending.clear()
        try:
            if error:
                raise ValueError(error)
            if not files or latest_task_id() != item_id:
                return
            self._writing = True
            copy_image_files(files)
        except (OSError, ValueError, RuntimeError) as exc:
            hooks.emit_toast('warning', f'Task saved, but could not copy its image: {exc}. Copy it from the F9 menu.')
        else:
            hooks.emit_toast('success', 'Added task to clipboard')
        finally:
            self._writing = False

    def _clipboard_changed(self):
        # A deliberate copy (one task, a selection, or another app) wins over
        # any automatic render already in flight. A later edit can copy again.
        if not self._writing:
            self._revision += 1
            self._pending.clear()
            self._timer.stop()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        self._pending.clear()
        QApplication.clipboard().dataChanged.disconnect(self._clipboard_changed)
        self._workers.shutdown(wait=False, cancel_futures=True)
