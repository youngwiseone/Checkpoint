"""Portable PNG cards: visible notes plus lossless UTF-8 context metadata."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from functools import wraps
from threading import RLock
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
from sqlalchemy import select

from ..config import paths
from ..db import read_session
from ..models import Capture, Project, WorkItem
from ..storage import atomic_write_bytes, media_path
from .export import _collect

WIDTH = 1200
LINE_HEIGHT = 34
LINES_PER_PAGE = 24
_cache: dict[str, tuple[str, tuple[Path, ...]]] = {}
_lock = RLock()


def _serialized(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _lock:
            return func(*args, **kwargs)
    return wrapped


def output_folder(project_id: str | None = None, status: str = 'open') -> Path:
    root = paths().root / 'images'
    if project_id is None:
        root.mkdir(parents=True, exist_ok=True)
        return root
    with read_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError('Project not found')
        name = re.sub(r'[^\w -]', '_', project.name).strip(' .')[:60] or 'Project'
        project_folder = root / f'{name}-{project.id}'
    label = {'open': 'Open', 'in_progress': 'Open', 'done': 'Done', 'wont_do': 'Done', 'deleted': 'Deleted', 'all': ''}.get(status)
    if label is None:
        raise ValueError('Unknown status')
    folder = project_folder / label if label else project_folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _font(size: int):
    for name in ("C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def context_text(item: dict) -> str:
    from .review import visible_context
    parts = [item['title'], f"Project: {item.get('project_name', '')} | Status: {item.get('work_status', '')}"]
    body = visible_context(item)
    if body:
        parts.extend(['', body])
    return '\n'.join(parts)


def _wrap(text: str, font, width: int) -> list[str]:
    # Split by measured glyph width, retaining whitespace and every character.
    result = []
    for paragraph in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if not paragraph:
            result.append('')
            continue
        remaining = paragraph
        while remaining:
            lo, hi = 1, len(remaining)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.getlength(remaining[:mid]) <= width:
                    lo = mid
                else:
                    hi = mid - 1
            cut = lo
            if cut < len(remaining):
                space = remaining.rfind(' ', 0, cut + 1)
                if space > 0:
                    cut = space + 1
            result.append(remaining[:cut])
            remaining = remaining[cut:]
    return result


def _export_item(item: dict) -> dict:
    result = dict(item)
    if item.get('description_edited') or item.get('description'):
        for key in ('excerpts', 'evidence'):
            if key in result:
                result[key] = [entry for entry in result[key] if entry.get('kind') != 'note']
    return result


def render_cards(item: dict, screenshot: Path | None = None, caption: str = '') -> list[bytes]:
    item = _export_item(item)
    text = context_text(item)
    font, small = _font(26), _font(20)
    lines = _wrap(text, font, WIDTH - 80)
    pages = [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]
    shot = None
    if screenshot is not None:
        with Image.open(screenshot) as source:
            shot = source.convert('RGB')
        shot.thumbnail((WIDTH, 850), Image.Resampling.LANCZOS)
    result = []
    for page, visible in enumerate(pages, 1):
        shot_height = shot.height if shot is not None else 0
        header = 64 if shot is not None else 0
        panel_y = header + shot_height
        image = Image.new('RGB', (WIDTH, panel_y + 195 + LINE_HEIGHT * len(visible)), '#f6f7fa')
        draw = ImageDraw.Draw(image)
        if shot is not None:
            draw.text((28, 20), caption or 'Checkpoint screenshot', font=small, fill='#495269')
            image.paste(shot, ((WIDTH - shot.width) // 2, header))
        draw.rectangle((0, panel_y, WIDTH, image.height), fill='#ffffff')
        draw.line((0, panel_y, WIDTH, panel_y), fill='#dbe0e9', width=2)
        draw.text((40, panel_y + 24), f'CHECKPOINT  /  NOTES  /  {page} of {len(pages)}', font=small, fill='#526585')
        y = panel_y + 65
        for line in visible:
            draw.text((40, y), line, font=font, fill='#1d2738')
            y += LINE_HEIGHT
        draw.text((40, y + 16), 'Read the notes above. Original PNG: exact text in Checkpoint.Context iTXt.', font=small, fill='#627189')
        draw.text((40, y + 44), 'Pillow: Image.open(path).info["Checkpoint.Context"]', font=small, fill='#627189')
        draw.text((40, y + 72), 'Uploads may drop metadata. Include every page when sharing.', font=small, fill='#627189')
        metadata = PngImagePlugin.PngInfo()
        metadata.add_itxt('Checkpoint.Context', text)
        metadata.add_itxt('Checkpoint.Item', json.dumps({'schema': 1, 'item': item, 'page': page, 'pages': len(pages), 'caption': caption}, ensure_ascii=False))
        buf = io.BytesIO()
        image.save(buf, 'PNG', pnginfo=metadata)
        result.append(buf.getvalue())
    return result


def _sources(item: dict, include_screenshots: bool):
    sources = []
    if include_screenshots:
        with read_session() as session:
            for sc in item.get('screenshots', []):
                capture = session.get(Capture, sc['id'])
                if capture:
                    path = media_path(capture.image_rel_path)
                    if path.is_file():
                        sources.append((path, f"Screenshot at {sc['offset']}" + (' (possibly related)' if sc['confidence'] != 'direct' else ''), capture.sha256))
    return sources or [(None, 'Text note', '')]


def _write_item(item: dict, folder: Path, include_screenshots: bool = True) -> str:
    item = _export_item(item)
    sources = _sources(item, include_screenshots)
    signature = hashlib.sha256((json.dumps(item, sort_keys=True, ensure_ascii=False) + str([(str(p), sha) for p, _, sha in sources])).encode()).hexdigest()
    key = str(folder / item['id'])
    cached = _cache.get(key)
    if cached and cached[0] == signature and all(path.is_file() for path in cached[1]):
        return signature
    written = set()
    for number, (source, caption, _) in enumerate(sources, 1):
        for page, data in enumerate(render_cards(item, source, caption), 1):
            target = folder / f"{item['id']}-{number:02d}-{page:03d}.png"
            atomic_write_bytes(target, data)
            written.add(target)
    for old in folder.glob(f"{item['id']}-*.png"):
        if old not in written:
            old.unlink()
    _cache[key] = (signature, tuple(written))
    return signature


def _owned_item(path: Path) -> str | None:
    match = re.fullmatch(r'([0-9a-f-]{36})-\d{2,}-\d{3,}\.png', path.name)
    return match.group(1) if match else None


@_serialized
def refresh() -> Path:
    """Write destination cards before removing previous project/status copies."""
    root = output_folder()
    with read_session() as session:
        ids = list(session.scalars(select(WorkItem.id)).all())
    items, _ = _collect(ids, True)
    successful: dict[str, Path] = {}
    for item in items:
        try:
            folder = output_folder(item['project_id'], item['work_status'])
            _write_item(item, folder)
            successful[item['id']] = folder
        except Exception:
            logging.getLogger(__name__).exception('Could not update PNG card %s', item['id'])
    # Never remove a source copy until its destination has been fully rendered.
    # Only application-generated filenames are eligible; user files stay untouched.
    for old in root.rglob('*.png'):
        iid = _owned_item(old)
        if iid and (iid not in ids or (iid in successful and old.parent != successful[iid])):
            old.unlink()
    legacy = paths().exports / 'cards'
    if legacy.is_dir():
        for old in legacy.glob('*.png'):
            iid = _owned_item(old)
            if iid and (iid in successful or iid not in ids):
                old.unlink()
    return root


@_serialized
def write_folder(item_ids: list[str], include_screenshots: bool = True, include_excerpts: bool = True) -> Path:
    items, _ = _collect(item_ids, include_excerpts)
    if not items:
        raise ValueError('Select at least one item to export')
    folder = paths().exports / 'images' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        _write_item(item, folder, include_screenshots)
    return folder
