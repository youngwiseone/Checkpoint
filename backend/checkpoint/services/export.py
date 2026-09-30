"""Export approved items as Markdown, JSON or a ZIP with screenshots.

Exports contain only the selected items, their linked evidence text and (optionally)
their screenshots. No tokens, settings, audio or unrelated transcript are included.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import read_session
from ..models import Project, WorkItem
from ..storage import media_path
from ..timebase import fmt_offset
from .review import work_item_dict

TYPE_LABEL = {"bug": "Bug", "improvement": "Improvement", "idea": "Idea", "task": "Task", "question": "Question", "note": "Note"}
STATUS_LABEL = {"open": "Open", "in_progress": "In progress", "done": "Done", "wont_do": "Won't do"}


def _collect(item_ids: list[str], include_excerpts: bool) -> tuple[list[dict], dict[str, str]]:
    items = []
    projects = {}
    with read_session() as s:
        for iid in item_ids:
            w = s.get(WorkItem, iid)
            if w is None:
                continue
            d = work_item_dict(s, w, with_evidence=True)
            if w.project_id not in projects:
                p = s.get(Project, w.project_id)
                projects[w.project_id] = p.name if p else ""
            d["project_name"] = projects[w.project_id]
            ev = d.pop("evidence", [])
            d["screenshots"] = [{"id": e["capture_id"], "offset": fmt_offset(e.get("offset_ms")), "confidence": e["confidence"],
                                 "path": None} for e in ev if e["kind"] == "capture"]
            d["excerpts"] = ([{"kind": e["kind"], "source": e.get("source_label") or ("Typed note" if e["kind"] == "note" else ""),
                               "offset": fmt_offset(e.get("offset_ms")), "text": e["text"], "role": e["role"]}
                              for e in ev if e["kind"] in ("segment", "note")] if include_excerpts else [])
            for k in ("conflict_server_copy", "sync_error", "shared_attachment_ids", "thumb_url"):
                d.pop(k, None)
            items.append(d)
    return items, projects


def _md(items: list[dict], with_images: bool) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Checkpoint export", "", f"Exported {now} · {len(items)} item(s)", ""]
    for it in items:
        lines.append(f"## [{TYPE_LABEL.get(it['type'], it['type'])}] {it['title']}")
        lines.append("")
        meta = [f"**Status:** {STATUS_LABEL.get(it['work_status'], it['work_status'])}", f"**Project:** {it['project_name']}"]
        if it.get("session_title"):
            meta.append(f"**Session:** {it['session_title']}")
        if it.get("assignee"):
            meta.append(f"**Assignee:** {it['assignee']}")
        if it.get("priority"):
            meta.append(f"**Priority:** {it['priority']}")
        if it.get("tags"):
            meta.append("**Tags:** " + ", ".join(it["tags"]))
        lines.append(" · ".join(meta))
        lines.append("")
        if it.get("description"):
            lines.append(it["description"])
            lines.append("")
        if it["excerpts"]:
            lines.append("**Context**")
            lines.append("")
            for e in it["excerpts"]:
                prefix = "Withdrawn by: " if e["role"] == "withdrawal" else ""
                lines.append(f"> [{e['offset']}] {e['source']}: {prefix}{e['text']}")
            lines.append("")
        if with_images:
            for sc in it["screenshots"]:
                if sc.get("path"):
                    note = " (possibly related)" if sc["confidence"] != "direct" else ""
                    lines.append(f"![Screenshot at {sc['offset']}{note}]({sc['path']})")
            lines.append("")
        lines.append(f"<sub>id {it['id']}</sub>")
        lines.append("")
    return "\n".join(lines)


def export(item_ids: list[str], fmt: str, include_screenshots: bool = True, include_excerpts: bool = True) -> tuple[bytes, str, str]:
    items, _ = _collect(item_ids, include_excerpts)
    if not items:
        raise ValueError("Select at least one item to export")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "json":
        return json.dumps({"exported_at": datetime.now(timezone.utc).isoformat(), "items": items}, indent=2).encode(), "application/json", f"checkpoint-{stamp}.json"
    if fmt == "md":
        return _md(items, False).encode(), "text/markdown; charset=utf-8", f"checkpoint-{stamp}.md"
    if fmt == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            if include_screenshots:
                with read_session() as s:
                    from ..models import Capture

                    for it in items:
                        for sc in it["screenshots"]:
                            c = s.get(Capture, sc["id"])
                            if c is None:
                                continue
                            p = media_path(c.image_rel_path)
                            if p.is_file():
                                rel = f"attachments/{c.id}.png"
                                z.write(p, rel)
                                sc["path"] = rel
            z.writestr("items.md", _md(items, include_screenshots))
            z.writestr("items.json", json.dumps({"exported_at": datetime.now(timezone.utc).isoformat(), "items": items}, indent=2))
        return buf.getvalue(), "application/zip", f"checkpoint-{stamp}.zip"
    raise ValueError("Unknown export format")
