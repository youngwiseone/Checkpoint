"""Extraction validation, corrections/withdrawals, screenshot association and reprocessing."""

from datetime import datetime, timedelta, timezone

from conftest import png_bytes


class FakeProvider:
    def __init__(self, chunk_fn, reconcile_fn=None):
        self.chunk_fn = chunk_fn
        self.reconcile_fn = reconcile_fn or (lambda user: {"links": []})
        self.calls = []

    def chat_json(self, model, system, user, schema, num_ctx=8192):
        self.calls.append((schema.__name__, user))
        if schema.__name__ == "ChunkResult":
            return schema.model_validate(self.chunk_fn(user, len([c for c in self.calls if c[0] == "ChunkResult"])))
        return schema.model_validate(self.reconcile_fn(user))


def _setup():
    from checkpoint.db import write_session
    from checkpoint.models import AudioSource, ChecklistEntry, Project, Session, TranscriptSegment, new_id
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager

    now = datetime.now(timezone.utc)
    lines = [
        (61_000, "Okay I'm on the cannon deck now."),                                         # S1
        (72_000, "Only the player aiming the cannon should see Land No Land."),               # S2
        (410_000, "Maybe the dash should be a bit longer."),                                  # S3
        (690_000, "Sir Spin A Lot keeps clipping through the wall."),                         # S4
        (698_000, "Only Sir Spin A Lot has this issue, the other bosses are fine."),          # S5
        (700_000, "I also checked the boss arena collisions, that's done."),                  # S6
        (1_520_000, "Actually, leave dash alone."),                                           # S7
    ]
    with write_session() as s:
        p = Project(name="Game", glossary="Sir Spin A Lot")
        s.add(p)
        s.flush()
        sess = Session(project_id=p.id, title="Playtest", state="ended", started_at=now - timedelta(hours=1), ended_at=now)
        s.add(sess)
        s.flush()
        src = AudioSource(session_id=sess.id, kind="mic", label="Me")
        s.add(src)
        s.flush()
        ids = []
        for off, text in lines:
            seg = TranscriptSegment(id=new_id(), session_id=sess.id, source_id=src.id, start_ms=off, end_ms=off + 3000, text=text)
            s.add(seg)
            ids.append(seg.id)
        s.add(ChecklistEntry(session_id=sess.id, project_id=p.id, text="Boss arena collisions", origin="planned"))
        pid, sid = p.id, sess.id
    cs = CaptureService(SessionManager())
    png, thumb = png_bytes()
    t = now
    cap_cannon = cs.import_image(png, thumb, 1, 1, project_id=pid, session_id=sid, offset_ms=75_000, taken_at=t, status="marker")
    cap_boss = cs.import_image(png, thumb, 1, 1, project_id=pid, session_id=sid, offset_ms=695_000, taken_at=t, status="marker")
    return pid, sid, ids, cap_cannon, cap_boss


def chunk_single(user, n):
    return {
        "items": [
            {"type": "bug", "title": "Only the player aiming the cannon should see Land / No Land",
             "description": "Shown to all players.", "statement": "observation", "source_ids": ["S2", "C1"]},
            {"type": "idea", "title": "Make the dash longer", "description": "Dash feels short (made-up quote here).",
             "statement": "withdrawn", "source_ids": ["S3"], "withdrawal_source_ids": ["S7"]},
            {"type": "bug", "title": "Sir Spin A Lot clips through the arena wall", "description": "Only Sir Spin A Lot; other bosses fine.",
             "statement": "observation", "source_ids": ["S4", "S5"]},
            {"type": "task", "title": "Invented item", "description": "x", "statement": "plan", "source_ids": ["S99"]},
        ],
        "checklist_possibly_done": [{"checklist_id": "K1", "source_ids": ["S6"]}],
    }


def test_pipeline_validates_and_applies_corrections(app_env):
    from checkpoint.extraction.pipeline import Pipeline
    from checkpoint.services import review as rv

    pid, sid, ids, cap_cannon, cap_boss = _setup()
    prov = FakeProvider(chunk_single)
    stats = Pipeline(prov, "fake", chunk_chars=100_000).run(sid)
    assert stats["rejected_invalid_ids"] == 1  # "S99" never becomes a card
    assert stats["chunks"] == 1 and [c[0] for c in prov.calls] == ["ChunkResult", "ReconcileResult"]

    drafts = {d["title"]: d for d in rv.list_drafts(session_id=sid)}
    assert "Invented item" not in drafts
    dash = drafts["Make the dash longer"]
    assert dash["withdrawn"] and dash["review_state"] == "dismissed"  # can't be approved by accident
    assert any(e["role"] == "withdrawal" and e["text"] == "Actually, leave dash alone." for e in dash["evidence"])
    # Quotes come from stored text, not model output.
    assert all("made-up" not in (e.get("text") or "") for e in dash["evidence"])

    boss = drafts["Sir Spin A Lot clips through the arena wall"]
    assert {e["text"] for e in boss["evidence"] if e["kind"] == "segment"} == {
        "Sir Spin A Lot keeps clipping through the wall.", "Only Sir Spin A Lot has this issue, the other bosses are fine."}
    boss_caps = [e for e in boss["evidence"] if e["kind"] == "capture"]
    assert [c["capture_id"] for c in boss_caps] == [cap_boss] and boss_caps[0]["confidence"] == "candidate"

    cannon = drafts["Only the player aiming the cannon should see Land / No Land"]
    assert [e["capture_id"] for e in cannon["evidence"] if e["kind"] == "capture"] == [cap_cannon]

    # Checklist: suggestion only, never auto-done.
    cl = rv.list_checklist(sid)
    assert cl[0]["suggested_done"] is True and cl[0]["state"] == "open"


def test_reprocess_preserves_human_edits_and_does_not_duplicate(app_env):
    from checkpoint.extraction.pipeline import Pipeline
    from checkpoint.services import review as rv

    pid, sid, ids, *_ = _setup()
    Pipeline(FakeProvider(chunk_single), "fake", chunk_chars=100_000).run(sid)
    first = {d["title"]: d for d in rv.list_drafts(session_id=sid)}
    boss_id = first["Sir Spin A Lot clips through the arena wall"]["id"]
    cannon_id = first["Only the player aiming the cannon should see Land / No Land"]["id"]
    rv.update_draft(boss_id, {"title": "Boss clipping (edited by me)"})
    rv.approve([cannon_id])

    def chunk_v2(user, n):
        r = chunk_single(user, n)
        r["items"][2]["title"] = "Model renamed boss issue"
        r["items"][0]["title"] = "Model renamed cannon issue"
        return r

    Pipeline(FakeProvider(chunk_v2), "fake", chunk_chars=100_000).run(sid)
    after = rv.list_drafts(session_id=sid)
    titles = [d["title"] for d in after]
    assert len(after) == 3  # no duplicates
    assert "Boss clipping (edited by me)" in titles  # human edit wins
    assert rv.get_draft(cannon_id)["review_state"] == "approved"
    assert "Model renamed cannon issue" not in titles  # approved card untouched


def test_multi_chunk_reconciliation_merges_withdrawal(app_env):
    from checkpoint.extraction.pipeline import Pipeline
    from checkpoint.services import review as rv

    pid, sid, *_ = _setup()

    def chunk_fn(user, n):
        items = []
        if "S3 [" in user and "(context) S3" not in user:
            items.append({"type": "idea", "title": "Longer dash", "description": "Dash could be longer.", "statement": "idea", "source_ids": ["S3"]})
        if "S7 [" in user and "(context) S7" not in user:
            items.append({"type": "note", "title": "Leave dash alone", "description": "Dash change withdrawn.", "statement": "withdrawn", "source_ids": ["S7"]})
        return {"items": items}

    def reconcile_fn(user):
        xs = [l.split(" ")[0] for l in user.splitlines() if l.startswith("X")]
        return {"links": [{"candidate_id": xs[-1], "relation": "withdraws", "target_id": xs[0]}]}

    prov = FakeProvider(chunk_fn, reconcile_fn)
    stats = Pipeline(prov, "fake", chunk_chars=120, overlap_items=1).run(sid)
    assert stats["chunks"] > 1
    assert any(c[0] == "ReconcileResult" for c in prov.calls)
    drafts = rv.list_drafts(session_id=sid)
    assert len(drafts) == 1
    d = drafts[0]
    assert d["withdrawn"] and d["review_state"] == "dismissed"
    roles = {(e["text"], e["role"]) for e in d["evidence"] if e["kind"] == "segment"}
    assert ("Maybe the dash should be a bit longer.", "source") in roles
    assert ("Actually, leave dash alone.", "withdrawal") in roles
    # Deliberate restore is possible.
    rv.restore(d["id"])
    assert rv.get_draft(d["id"])["review_state"] == "pending"


def test_screenshot_overlapping_two_cards_is_uncertain(app_env):
    from checkpoint.extraction.pipeline import Pipeline
    from checkpoint.services import review as rv

    pid, sid, ids, cap_cannon, cap_boss = _setup()

    def chunk_fn(user, n):
        return {"items": [
            {"type": "bug", "title": "Clipping", "description": "", "statement": "observation", "source_ids": ["S4"]},
            {"type": "bug", "title": "Scope", "description": "", "statement": "observation", "source_ids": ["S5"]},
        ]}

    Pipeline(FakeProvider(chunk_fn), "fake", chunk_chars=100_000).run(sid)
    for d in rv.list_drafts(session_id=sid):
        caps = [e for e in d["evidence"] if e["kind"] == "capture"]
        assert [c["capture_id"] for c in caps] == [cap_boss]
        assert caps[0]["confidence"] == "uncertain"


def test_typed_note_is_authoritative_and_not_duplicated(app_env):
    from checkpoint.extraction.pipeline import Pipeline
    from checkpoint.services import review as rv
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager
    from checkpoint.db import read_session
    from checkpoint.models import Note

    pid, sid, ids, cap_cannon, cap_boss = _setup()
    cs = CaptureService(SessionManager())
    cs.save_capture_note(cap_cannon, "Land/No Land visible to everyone")
    with read_session() as s:
        n = s.query(Note).one()
        n.offset_ms  # noqa: B018

    def chunk_fn(user, n):
        return {"items": [
            {"type": "bug", "title": "Model version of the typed note", "description": "", "statement": "observation", "source_ids": ["N1"]},
            {"type": "bug", "title": "Model merges note and speech", "description": "", "statement": "observation", "source_ids": ["N1", "S2"]},
        ]}

    Pipeline(FakeProvider(chunk_fn), "fake", chunk_chars=100_000).run(sid)
    drafts = rv.list_drafts(session_id=sid)
    assert [d["title"] for d in drafts] == ["Land/No Land visible to everyone"]
    ev = drafts[0]["evidence"]
    assert any(e["kind"] == "segment" and e["role"] == "context" for e in ev)  # speech attached as context only


def test_timebase_offsets_preserve_gaps():
    import time

    from checkpoint.timebase import SessionClock

    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    c = SessionClock(start)
    off = c.offset_ms()
    assert 599_000 < off < 601_500
    later = c.offset_ms(time.monotonic() + 5)
    assert 4_900 < later - off < 5_100
    assert SessionClock.offset_for_utc(start, start + timedelta(seconds=90)) == 90_000
