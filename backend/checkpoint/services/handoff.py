"""Hand approved items to an AI assistant (Claude Desktop, Codex, or any chat by copy/paste).

A handoff is a saved selection of items. Assistants connected to the Checkpoint MCP tool fetch
it by code (H-12); the bundle contains the item text, linked excerpts and screenshots resized
to a size assistants accept. Nothing is sent anywhere by Checkpoint itself.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select

from ..config import paths
from ..db import read_session, write_session
from ..models import Capture, Handoff, WorkItem, utcnow
from ..storage import atomic_write_bytes, media_path
from .export import STATUS_LABEL, TYPE_LABEL, _collect

MAX_IMAGES = 12
MAX_EDGE = 1600


@dataclass
class Bundle:
    code: str
    title: str
    markdown: str
    images: list[tuple[str, Path]] = field(default_factory=list)  # (caption, jpeg path)
    image_ids: list[str] = field(default_factory=list)  # capture ids, same order
    items: list[dict] = field(default_factory=list)
    omitted_images: int = 0


def handoff_code(n: int) -> str:
    return f"H-{n}"


# What the assistant is asked to do with a handoff. The wording follows what was sent: bugs get
# "fix", features/improvements/tasks get "implement", mixed selections get both. Session
# evidence can be wrong (speech-to-text, "possibly related" screenshots), so it is always
# checked against the code.
MODES = ("implement_commit", "implement", "investigate", "read")
_MODE_ALIASES = {"fix_commit": "implement_commit", "fix": "implement"}
_EVIDENCE = ("Use the notes, quotes and screenshots as evidence, but check them against the code: speech-to-text can be "
             "wrong and \"possibly related\" screenshots may not be.")


def _kind(types: set[str]) -> str:
    if not types:
        return "mixed"
    if types <= {"bug"}:
        return "bugs"
    return "features" if "bug" not in types else "mixed"


BATCH = ("Work through the items one at a time, in the order listed, without waiting for me between them. "
         "Only stop to ask if one is blocked on a decision; otherwise note it and move on. "
         "When you've finished, give me a short list: each item's title and what happened (done, needs a decision, "
         "couldn't reproduce).")


def task_text(mode: str, extra: str = "", types: set[str] | None = None, count: int = 1) -> str:
    mode = _MODE_ALIASES.get(mode, mode)
    if mode not in MODES:
        raise ValueError("Unknown mode")
    kind = _kind(set(types or ()))
    text = ""
    if mode in ("implement_commit", "implement"):
        intro = {
            "bugs": "Please fix each of these bugs in this project:",
            "features": "Please implement each of these items in this project:",
            "mixed": "Please work through each of these items in this project: fix the bugs and implement the other changes.",
        }[kind]
        understand = {
            "bugs": f"Find the cause. {_EVIDENCE}",
            "features": f"Work out exactly what is being asked for and where it belongs in the code. {_EVIDENCE}",
            "mixed": f"For bugs, find the cause; for features and improvements, work out exactly what is being asked for and where it belongs. {_EVIDENCE}",
        }[kind]
        change = {
            "bugs": "Make the smallest change that fixes it.",
            "features": "Implement it with a focused change that fits the existing code. Don't add anything that wasn't asked for.",
            "mixed": "Keep changes focused: the smallest fix for each bug, and only what was asked for each feature.",
        }[kind]
        steps = [understand, change, "Verify it works: build, run the relevant tests, or try it out if you can."]
        if mode == "implement_commit":
            noun = {"bugs": "fix", "features": "change", "mixed": "change"}[kind]
            steps.append(f"Once it's working, commit each {noun} with a clear message that mentions the Checkpoint item. "
                         "Only commit related changes.")
        lines = [intro] + [f"{n}. {st}" for n, st in enumerate(steps, start=1)]
        if mode == "implement":
            lines.append("Don't commit; leave the changes for me to review.")
        if kind in ("bugs", "mixed"):
            lines.append("If you can't find or confirm a bug's cause, tell me what you found instead of guessing.")
        if kind in ("features", "mixed"):
            lines.append("If a request is unclear or needs a bigger design decision, ask me before building it.")
        text = "\n".join(lines)
    elif mode == "investigate":
        what = {
            "bugs": "find the likely cause in the code and propose a fix",
            "features": "propose how you would implement it (where it belongs and what would change)",
            "mixed": "for bugs, find the likely cause and propose a fix; for features and improvements, propose how you would implement them",
        }[kind]
        text = f"Please investigate each item: {what}. {_EVIDENCE} Don't change any files yet."
    if count > 1 and mode != "read":
        text += "\n\n" + BATCH
    parts = [text]
    extra = (extra or "").strip()
    if extra:
        parts.append(("Additional notes: " if text else "") + extra)
    return "\n\n".join(p for p in parts if p)


def item_types(item_ids: list[str]) -> set[str]:
    with read_session() as s:
        return {w.type for w in (s.get(WorkItem, i) for i in item_ids) if w is not None}


def create(item_ids: list[str], include_screenshots: bool, include_excerpts: bool, instruction: str = "",
           mode: str = "implement_commit") -> dict:
    ids = list(dict.fromkeys(item_ids))
    instruction = task_text(mode, instruction, item_types(ids), len(ids))
    if not ids:
        raise ValueError("Select at least one item to send")
    with write_session() as s:
        items = [s.get(WorkItem, i) for i in ids]
        if any(w is None for w in items):
            raise ValueError("Item not found")
        sessions = {w.session_id for w in items}
        title = f"{len(items)} item{'s' if len(items) != 1 else ''}"
        if len(sessions) == 1 and items[0].session_id:
            from ..models import Session

            sess = s.get(Session, items[0].session_id)
            if sess:
                title += f" from “{sess.title}”"
        n = (s.scalar(select(func.max(Handoff.number))) or 0) + 1
        h = Handoff(number=n, title=title, item_ids=ids, include_screenshots=include_screenshots,
                    include_excerpts=include_excerpts, instruction=(instruction or "").strip()[:4000])
        s.add(h)
        s.flush()
        return {"id": h.id, "code": handoff_code(n), "title": title, "prompt": assistant_prompt(handoff_code(n), title, h.instruction)}


def assistant_prompt(code: str, title: str, instruction: str) -> str:
    lines = [f"I've sent you {title} from Checkpoint (handoff {code}).",
             f"Fetch it with the Checkpoint tool get_handoff (handoff \"{code}\"), including the screenshots."]
    if instruction:
        lines += ["", instruction]
    return "\n".join(lines)


def resolve(ref: str = "latest") -> Optional[Handoff]:
    ref = (ref or "latest").strip()
    with read_session() as s:
        if ref.lower() in ("", "latest", "last"):
            return s.scalars(select(Handoff).order_by(Handoff.number.desc())).first()
        num = ref.upper().removeprefix("H-").removeprefix("H")
        if num.isdigit():
            return s.scalars(select(Handoff).where(Handoff.number == int(num))).first()
        return s.get(Handoff, ref)


def list_recent(limit: int = 10) -> list[dict]:
    with read_session() as s:
        rows = s.scalars(select(Handoff).order_by(Handoff.number.desc()).limit(limit)).all()
        return [{"code": handoff_code(h.number), "title": h.title, "created_at": h.created_at.isoformat(), "items": len(h.item_ids or [])}
                for h in rows]


def mark_fetched(handoff_id: str) -> None:
    try:
        with write_session() as s:
            h = s.get(Handoff, handoff_id)
            if h:
                h.fetched_at = utcnow()
    except Exception:  # noqa: BLE001 - never fail a read because of bookkeeping
        pass


def _ai_image(capture_id: str, folder: Path) -> Optional[Path]:
    """Screenshot resized to a size assistants accept (long edge 1600 px, JPEG)."""
    from PIL import Image

    with read_session() as s:
        c = s.get(Capture, capture_id)
        if c is None:
            return None
        src = media_path(c.image_rel_path)
    if not src.is_file():
        return None
    out = folder / f"{capture_id}.jpg"
    if out.is_file():
        return out
    img = Image.open(src).convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    atomic_write_bytes(out, buf.getvalue())
    return out


def items_markdown(items: list[dict], images: dict[str, str], heading: str = "") -> str:
    lines = [heading, ""] if heading else []
    for it in items:
        lines.append(f"## [{TYPE_LABEL.get(it['type'], it['type'])}] {it['title']}")
        meta = [f"Status: {STATUS_LABEL.get(it['work_status'], it['work_status'])}", f"Project: {it['project_name']}"]
        if it.get("session_title"):
            meta.append(f"Session: {it['session_title']}")
        for k, label in (("assignee", "Assignee"), ("priority", "Priority")):
            if it.get(k):
                meta.append(f"{label}: {it[k]}")
        if it.get("tags"):
            meta.append("Tags: " + ", ".join(it["tags"]))
        lines.append(" · ".join(meta))
        lines.append(f"Item id: {it['id']}")
        lines.append("")
        if it.get("description"):
            lines += [it["description"], ""]
        if it.get("excerpts"):
            lines.append("What was said/typed during the session (verbatim; speech-to-text may contain errors):")
            for e in it["excerpts"]:
                prefix = "LATER WITHDRAWN BY: " if e["role"] == "withdrawal" else ""
                lines.append(f"> [{e['offset']}] {e['source']}: {prefix}{e['text']}")
            lines.append("")
        shots = [sc for sc in it.get("screenshots", []) if sc["id"] in images]
        for sc in shots:
            note = "" if sc["confidence"] == "direct" else " (possibly related: taken near this remark)"
            lines.append(f"Screenshot {images[sc['id']]} at {sc['offset']}{note}")
        if shots:
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def build(item_ids: list[str], include_screenshots: bool, include_excerpts: bool, code: str = "", title: str = "",
          instruction: str = "") -> Bundle:
    items, _ = _collect(item_ids, include_excerpts)
    if not items:
        raise ValueError("None of the selected items exist any more")
    folder = paths().exports / "ai" / (code or "copy")
    folder.mkdir(parents=True, exist_ok=True)
    images: dict[str, str] = {}
    files: list[tuple[str, Path]] = []
    omitted = 0
    if include_screenshots:
        for it in items:
            for sc in it["screenshots"]:
                if sc["id"] in images:
                    continue
                if len(files) >= MAX_IMAGES:
                    omitted += 1
                    continue
                p = _ai_image(sc["id"], folder)
                if p is None:
                    continue
                label = f"#{len(files) + 1}"
                images[sc["id"]] = label
                files.append((f"Screenshot {label} — {it['title']} ({sc['offset']})", p))
    head = f"# Checkpoint handoff {code}: {title}" if code else "# Items from Checkpoint"
    md = items_markdown(items, images, head)
    if instruction:
        md = f"{md}\nWhat the user asked for:\n{instruction}\n"
    if omitted:
        md += f"\n({omitted} more screenshot(s) not included; fetch a single item with get_item to see its screenshots.)\n"
    return Bundle(code=code, title=title, markdown=md, images=files, image_ids=list(images), items=items, omitted_images=omitted)


def bundle_for(h: Handoff) -> Bundle:
    return build(h.item_ids or [], h.include_screenshots, h.include_excerpts, handoff_code(h.number), h.title, h.instruction)


def write_folder(item_ids: list[str], include_screenshots: bool, include_excerpts: bool) -> Path:
    """For assistants without the connector: a folder with items.md + screenshots, opened in Explorer."""
    from datetime import datetime

    b = build(item_ids, include_screenshots, include_excerpts)
    folder = paths().exports / "ai" / datetime.now().strftime("copy-%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    names: dict[str, str] = {}
    for i, ((_cap, p), cid) in enumerate(zip(b.images, b.image_ids), start=1):
        names[cid] = f"screenshot-{i}.jpg"
        (folder / names[cid]).write_bytes(p.read_bytes())
    md = items_markdown(b.items, names, "# Items from Checkpoint")
    (folder / "items.md").write_text(md, encoding="utf-8")
    if os.name == "nt":
        os.startfile(folder)  # noqa: S606 - opens our own export folder in Explorer
    return folder
