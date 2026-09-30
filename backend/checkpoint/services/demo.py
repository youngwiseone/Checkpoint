"""Opt-in demo project with clearly labelled synthetic data.

Everything created here is flagged is_demo and can be removed in one action. The
demo never simulates live recording, meters or a successful AI run: cards in the
demo are marked with origin "demo".
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from ..db import write_session
from ..models import (
    AudioSource,
    ChecklistEntry,
    DraftItem,
    EvidenceLink,
    Note,
    Project,
    Session,
    TranscriptSegment,
    new_id,
)
from ..storage import media_path, remove_tree
from ..config import paths


def _font(size: int):  # noqa: ANN202
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _image(kind: str) -> tuple[bytes, bytes, int, int]:
    w, h = 1600, 900
    img = Image.new("RGB", (w, h), (24, 26, 32))
    d = ImageDraw.Draw(img)
    big, mid, small = _font(46), _font(30), _font(22)
    if kind == "game":
        img.paste((58, 96, 140), (0, 0, w, 520))
        img.paste((70, 120, 60), (0, 520, w, h))
        d.ellipse((620, 380, 820, 580), fill=(90, 90, 100))
        d.rectangle((700, 330, 900, 360), fill=(40, 40, 45))
        d.rounded_rectangle((980, 120, 1480, 260), 18, fill=(15, 15, 20))
        d.text((1010, 140), "LAND / NO LAND", font=big, fill=(250, 210, 80))
        d.text((1010, 205), "shown to: all players", font=small, fill=(220, 220, 220))
        d.text((40, 30), "Player 2 (not aiming) — cannon deck", font=mid, fill=(255, 255, 255))
    elif kind == "report":
        img.paste((245, 246, 248), (0, 0, w, h))
        d.rectangle((0, 0, w, 70), fill=(35, 60, 110))
        d.text((30, 18), "Sales overview — Power BI", font=mid, fill=(255, 255, 255))
        d.rounded_rectangle((40, 110, 520, 250), 12, fill=(255, 255, 255), outline=(210, 210, 215))
        d.text((60, 125), "Date range", font=small, fill=(90, 90, 100))
        d.text((60, 165), "01 Jan – Today (30 Sep)", font=mid, fill=(30, 30, 30))
        d.text((60, 210), "Latest loaded data: 27 Sep", font=small, fill=(180, 60, 60))
        for i, v in enumerate([320, 410, 380, 520, 470, 610, 580, 90]):
            x = 600 + i * 110
            d.rectangle((x, 820 - v, x + 70, 820), fill=(60, 110, 200) if i < 7 else (200, 200, 205))
        d.text((600, 840), "Last bar is empty because today's data isn't loaded yet", font=small, fill=(120, 120, 130))
    else:
        img.paste((250, 250, 250), (0, 0, w, h))
        d.text((40, 40), "Filters", font=big, fill=(30, 30, 30))
        for i, lbl in enumerate(["Region: All", "Owner: Me", "Status: Open"]):
            d.rounded_rectangle((40, 130 + i * 80, 460, 190 + i * 80), 10, outline=(180, 180, 190), fill=(255, 255, 255))
            d.text((60, 145 + i * 80), lbl, font=small, fill=(40, 40, 40))
        d.rounded_rectangle((40, 400, 260, 460), 10, fill=(60, 110, 200))
        d.text((62, 415), "Reset filters", font=small, fill=(255, 255, 255))
    d.text((w - 330, h - 44), "DEMO · synthetic image", font=small, fill=(200, 80, 80))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    t = img.copy()
    t.thumbnail((480, 480))
    tb = io.BytesIO()
    t.save(tb, "JPEG", quality=82)
    return buf.getvalue(), tb.getvalue(), w, h


def create_demo(capture_service) -> str:  # noqa: ANN001
    now = datetime.now(timezone.utc)
    with write_session() as s:
        existing = s.scalars(select(Project).where(Project.is_demo.is_(True))).first()
        if existing:
            return existing.id
        p = Project(name="Demo — sample sessions", is_demo=True,
                    description="Synthetic examples showing how Checkpoint organises a playtest, a report review and solo work.",
                    glossary="Sir Spin A Lot, Land / No Land, dash")
        s.add(p)
        s.flush()
        pid = p.id
        game = Session(project_id=pid, title="Demo · Co-op playtest with Sam", purpose="Check the cannon deck and boss fights",
                       state="ended", started_at=now - timedelta(days=1, minutes=40), ended_at=now - timedelta(days=1),
                       transcription_mode="after", is_demo=True, processing_state="done")
        report = Session(project_id=pid, title="Demo · Sales report review", purpose="Review the date range logic",
                         state="ended", started_at=now - timedelta(hours=5), ended_at=now - timedelta(hours=4, minutes=45),
                         transcription_mode="off", is_demo=True)
        solo = Session(project_id=pid, title="Demo · Solo work", state="ended", started_at=now - timedelta(hours=2),
                       ended_at=now - timedelta(hours=1, minutes=50), transcription_mode="off", is_demo=True)
        s.add_all([game, report, solo])
        s.flush()
        mic = AudioSource(session_id=game.id, kind="mic", label="Me", device_name="(demo)", state="stopped")
        call = AudioSource(session_id=game.id, kind="loopback", label="Call audio", device_name="(demo)", state="stopped")
        s.add_all([mic, call])
        s.flush()
        lines = [
            (mic, 61_000, "Okay, I'm on the cannon deck now."),
            (call, 66_000, "I can see Land / No Land on my screen too, and I'm not even aiming."),
            (mic, 72_000, "Yeah, only the player aiming the cannon should see Land / No Land."),
            (mic, 410_000, "Maybe the dash should be a bit longer, it feels short."),
            (call, 690_000, "Sir Spin A Lot keeps clipping through the arena wall."),
            (mic, 698_000, "Only Sir Spin A Lot has this issue, the other bosses are fine."),
            (mic, 1_520_000, "Actually, leave dash alone. It's fine once you get used to it."),
            (call, 1_800_000, "The new music on level two is great by the way."),
        ]
        segs = []
        for src, off, text in lines:
            seg = TranscriptSegment(id=new_id(), session_id=game.id, source_id=src.id, chunk_id=None, start_ms=off,
                                    end_ms=off + 3500, text=text, confidence=0.9, model="demo")
            s.add(seg)
            segs.append(seg)
        s.add(ChecklistEntry(session_id=game.id, project_id=pid, text="Cannon deck targeting UI", origin="planned", position=0, state="done",
                             completed_at=now - timedelta(days=1, minutes=30)))
        s.add(ChecklistEntry(session_id=game.id, project_id=pid, text="Boss arena collisions", origin="planned", position=1))
        s.add(ChecklistEntry(session_id=solo.id, project_id=pid, text="Reset-filters button", origin="planned", position=0))
        s.flush()

        def card(sess, type_, title, desc, seg_idx=(), state="pending", withdrawn=False, statement="observation", wd_idx=()):  # noqa: ANN001, ANN202
            d = DraftItem(project_id=pid, session_id=sess.id, origin="demo", type=type_, title=title, description=desc,
                          review_state=state, withdrawn=withdrawn, statement_kind=statement)
            s.add(d)
            s.flush()
            for i in seg_idx:
                s.add(EvidenceLink(draft_id=d.id, segment_id=segs[i].id, confidence="direct", role="source", attached_by="system"))
            for i in wd_idx:
                s.add(EvidenceLink(draft_id=d.id, segment_id=segs[i].id, confidence="direct", role="withdrawal", attached_by="system"))
            return d

        cannon = card(game, "bug", "Only the player aiming the cannon should see Land / No Land",
                      "Land / No Land is currently shown to every player on the cannon deck, including players who aren't aiming.", (1, 2))
        card(game, "bug", "Sir Spin A Lot clips through the arena wall",
             "Sir Spin A Lot clips through the arena wall. Other bosses are fine.", (4, 5))
        dash = card(game, "idea", "Make the dash slightly longer", "The dash feels short.", (3,), state="dismissed",
                    withdrawn=True, statement="withdrawn", wd_idx=(6,))
        dash.dismissed_at = now - timedelta(days=1)
        card(game, "note", "Level two music is well liked", "The new music on level two got positive feedback.", (7,))
        game_id, report_id, solo_id, cannon_id = game.id, report.id, solo.id, cannon.id

    png, thumb, w, h = _image("game")
    cid = capture_service.import_image(png, thumb, w, h, project_id=pid, session_id=game_id, offset_ms=70_000,
                                       taken_at=now - timedelta(days=1, minutes=39), status="marker")
    png2, thumb2, w2, h2 = _image("report")
    cid2 = capture_service.import_image(png2, thumb2, w2, h2, project_id=pid, session_id=report_id, offset_ms=95_000,
                                        taken_at=now - timedelta(hours=4, minutes=58), status="saved")
    with write_session() as s:
        s.add(EvidenceLink(draft_id=cannon_id, capture_id=cid, confidence="candidate", role="context", attached_by="system"))
        text = "Use the latest loaded date instead of today's date for this range"
        n = Note(session_id=report_id, project_id=pid, capture_id=cid2, text=text, kind="typed",
                 taken_at=now - timedelta(hours=4, minutes=58), offset_ms=95_000)
        s.add(n)
        s.flush()
        d = DraftItem(project_id=pid, session_id=report_id, origin="demo", type="improvement", title=text, description=text)
        s.add(d)
        s.flush()
        s.add(EvidenceLink(draft_id=d.id, capture_id=cid2, confidence="direct", attached_by="user"))
        s.add(EvidenceLink(draft_id=d.id, note_id=n.id, confidence="direct", attached_by="user"))
        text2 = "Remember to check the reset-filters button"
        n2 = Note(session_id=solo_id, project_id=pid, text=text2, kind="quick", taken_at=now - timedelta(hours=1, minutes=55), offset_ms=300_000)
        s.add(n2)
        s.flush()
        d2 = DraftItem(project_id=pid, session_id=solo_id, origin="demo", type="task", title=text2, description=text2)
        s.add(d2)
        s.flush()
        s.add(EvidenceLink(draft_id=d2.id, note_id=n2.id, confidence="direct", attached_by="user"))
    return pid


def remove_demo() -> int:
    n = 0
    with write_session() as s:
        projects = s.scalars(select(Project).where(Project.is_demo.is_(True))).all()
        sids = []
        for p in projects:
            sids += [x.id for x in s.scalars(select(Session).where(Session.project_id == p.id)).all()]
            s.delete(p)
            n += 1
    for sid in sids:
        remove_tree(paths().media / sid, paths().media)
        remove_tree(paths().audio / sid, paths().audio)
    return n
