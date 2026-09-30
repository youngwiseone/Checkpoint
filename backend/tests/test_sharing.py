"""Publish → shared server (real HTTP, SQLite stand-in for PostgreSQL) → second client; retries, conflicts, offline."""

import socket
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest

from conftest import png_bytes

TOKEN = "test-token-" + "x" * 40


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server(tmp_path, monkeypatch):
    import uvicorn

    from checkpoint_server.app import Config, create_app, run_migrations

    url = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    monkeypatch.setenv("CHECKPOINT_SERVER_DATABASE_URL", url)
    monkeypatch.setenv("CHECKPOINT_SERVER_TOKEN", TOKEN)
    monkeypatch.setenv("CHECKPOINT_SERVER_MEDIA_DIR", str(tmp_path / "server-media"))
    monkeypatch.setenv("CHECKPOINT_SERVER_WORKSPACE_NAME", "Test team")
    run_migrations(url)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(Config()), host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    srv.install_signal_handlers = lambda: None
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


@pytest.fixture()
def desktop(app_env, server, monkeypatch):
    from checkpoint.sharing import client as sc
    from checkpoint.settings_store import update_settings

    tokens = {}
    monkeypatch.setattr(sc, "save_token", lambda u, t: tokens.__setitem__(u, t))
    monkeypatch.setattr(sc, "load_token", lambda u: tokens.get(u))
    update_settings({"sharing": {"server_url": server, "display_name": "Alex", "allow_insecure_private_network": True}})
    sc.save_token(server, TOKEN)
    return server


def _approved_item_with_screenshot(shared: bool, server_url: str):
    from checkpoint.db import write_session
    from checkpoint.models import Project
    from checkpoint.services import review as rv
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager
    from checkpoint.sharing.client import SharedClient
    import uuid

    spid = str(uuid.uuid4()) if shared else None
    with write_session() as s:
        p = Project(name="Team game" if shared else "Private", shared_project_id=spid, shared_project_name="Team game" if shared else None)
        s.add(p)
        s.flush()
        pid = p.id
    if shared:
        with SharedClient() as c:
            c.put_project(spid, "Team game")
    cs = CaptureService(SessionManager())
    png, thumb = png_bytes()
    cid = cs.import_image(png, thumb, 320, 180, project_id=pid, session_id=None, offset_ms=None,
                          taken_at=datetime.now(timezone.utc), status="unfinished")
    r = cs.save_capture_note(cid, "Only the player aiming the cannon should see Land / No Land", "bug")
    wid = rv.approve([r["draft_id"]])[0]["work_item_id"]
    return pid, spid, wid, cid, png


def _drain(worker, n=10):
    for _ in range(n):
        if not worker.step():
            break


def test_publish_retrieve_update_and_conflict(desktop):
    from checkpoint.services import review as rv
    from checkpoint.sharing.client import SyncWorker, publish, refresh_project, resolve_conflict

    pid, spid, wid, cid, png = _approved_item_with_screenshot(True, desktop)
    assert rv.get_work_item(wid)["sharing_state"] == "local_only"  # approval alone uploads nothing

    publish([wid], include_screenshots=True, include_excerpts=False)
    publish([wid], include_screenshots=True, include_excerpts=False)  # double click → still one job
    w = SyncWorker()
    _drain(w)
    item = rv.get_work_item(wid)
    assert item["sharing_state"] == "synced" and item["server_version"] == 1

    # Retry publishing: idempotent on the server (no duplicate, no version bump).
    publish([wid], include_screenshots=True, include_excerpts=False)
    _drain(w)
    b = httpx.Client(base_url=desktop, headers={"Authorization": f"Bearer {TOKEN}", "X-Checkpoint-Display-Name": "Sam"})
    items = b.get(f"/api/v1/projects/{spid}/items").json()
    assert len(items) == 1 and items[0]["version"] == 1 and items[0]["missing_attachments"] == []
    # Second client downloads the screenshot through the authenticated endpoint.
    att = items[0]["attachments"][0]["id"]
    assert b.get(f"/api/v1/attachments/{att}").content == png
    assert httpx.get(f"{desktop}/api/v1/attachments/{att}").status_code == 401

    # Second client updates work status using optimistic concurrency.
    body = {k: items[0][k] for k in ("type", "title", "description", "assignee", "priority", "tags", "attachments", "excerpts", "session_title")}
    r = b.put(f"/api/v1/items/{wid}", json={**body, "project_id": spid, "base_version": 1, "work_status": "in_progress"})
    assert r.status_code == 200 and r.json()["version"] == 2
    stale = b.put(f"/api/v1/items/{wid}", json={**body, "project_id": spid, "base_version": 1, "work_status": "done"})
    assert stale.status_code == 409

    # Client A edits locally without refreshing → conflict surfaced, nothing overwritten.
    rv.update_work_item(wid, {"title": "Cannon: Land / No Land visible to all"})
    _drain(w)
    item = rv.get_work_item(wid)
    assert item["sharing_state"] == "conflict"
    assert b.get(f"/api/v1/items/{wid}").json()["work_status"] == "in_progress"
    resolve_conflict(wid, "local")
    _drain(w)
    srv = b.get(f"/api/v1/items/{wid}").json()
    assert srv["title"] == "Cannon: Land / No Land visible to all" and srv["version"] == 3
    assert rv.get_work_item(wid)["sharing_state"] == "synced"

    # Items created by the other client appear on refresh.
    import uuid

    other = str(uuid.uuid4())
    b.put(f"/api/v1/items/{other}", json={"project_id": spid, "type": "task", "title": "Check boss arena", "work_status": "open"})
    res = refresh_project(pid)
    assert res["added"] == 1
    mine = [i for i in rv.list_work_items(pid) if i["id"] == other][0]
    assert mine["origin"] == "shared" and mine["created_by"] == "Sam"


def test_offline_keeps_item_queued_and_local_only_never_leaks(desktop):
    from checkpoint.services import review as rv
    from checkpoint.settings_store import update_settings
    from checkpoint.sharing import client as sc
    from checkpoint.sharing.client import SharingError, SyncWorker, publish

    _pid, _spid, wid, _cid, _ = _approved_item_with_screenshot(True, desktop)
    dead = f"http://127.0.0.1:{_free_port()}"
    sc.save_token(dead, TOKEN)
    update_settings({"sharing": {"server_url": dead}})
    publish([wid], include_screenshots=True, include_excerpts=False)
    SyncWorker().step()
    item = rv.get_work_item(wid)
    assert item["sharing_state"] == "pending" and "waiting to sync" in item["sync_error"]

    _p2, _s2, private_wid, _c2, _ = _approved_item_with_screenshot(False, desktop)
    with pytest.raises(SharingError):
        publish([private_wid], include_screenshots=True, include_excerpts=True)
    assert rv.get_work_item(private_wid)["sharing_state"] == "local_only"
