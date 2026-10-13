"""Copy complete task PNGs as images and pasteable files."""
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


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
