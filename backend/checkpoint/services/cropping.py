"""Reversible crops and highlights. Consumers use the current image and thumbnail."""
import io

from PIL import Image, ImageDraw

from ..db import write_session
from sqlalchemy import select

from ..models import Capture, EvidenceLink, WorkItem, new_id, utcnow
from ..storage import atomic_write_bytes, media_path, sha256_bytes


def original_path(capture):
    return media_path(capture.image_rel_path).parent / f"{capture.id}_original.png"


def crop(capture_id: str, box: tuple[int, int, int, int] | None, *, highlight: bool = False) -> dict:
    with write_session() as s:
        c = s.get(Capture, capture_id)
        if c is None or c.status == "discarded":
            raise ValueError("Screenshot not found")
        current = media_path(c.image_rel_path)
        original = original_path(c)
        source = current if highlight else (original if original.exists() else current)
        with Image.open(source) as image:
            image.load()
            if box is not None:
                x, y, width, height = box
                if x < 0 or y < 0 or width < 1 or height < 1 or x + width > image.width or y + height > image.height:
                    raise ValueError("Selection must fit inside the screenshot")
                if highlight:
                    image = image.convert("RGB")
                    ImageDraw.Draw(image).rectangle((x, y, x + width - 1, y + height - 1), outline=(255, 64, 64),
                                                   width=max(2, round(max(image.size) / 400)))
                else:
                    image = image.crop((x, y, x + width, y + height))
            image = image.convert("RGB")
            png = io.BytesIO()
            image.save(png, "PNG")
            width, height = image.size
            image.thumbnail((480, 270))
            thumb = io.BytesIO()
            image.save(thumb, "JPEG", quality=85)
        if not original.exists():
            atomic_write_bytes(original, current.read_bytes())
        # New filenames keep cached images and concurrent readers consistent.
        folder = c.image_rel_path.rsplit("/", 1)[0]
        revision = new_id()
        image_rel = f"{folder}/{c.id}_{revision}.png"
        thumb_rel = f"{folder}/{c.id}_{revision}_thumb.jpg"
        atomic_write_bytes(media_path(image_rel), png.getvalue())
        atomic_write_bytes(media_path(thumb_rel), thumb.getvalue())
        c.image_rel_path, c.thumb_rel_path = image_rel, thumb_rel
        c.width, c.height, c.sha256 = width, height, sha256_bytes(png.getvalue())
        linked_ids = s.scalars(select(EvidenceLink.work_item_id).where(EvidenceLink.capture_id == c.id, EvidenceLink.work_item_id.is_not(None))).all()
        for iid in linked_ids:
            item = s.get(WorkItem, iid)
            if item is not None:
                item.human_updated_at = utcnow()
        return {"id": c.id, "width": width, "height": height, "revision": revision}
