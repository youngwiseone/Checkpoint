"""OllamaProvider + ExtractionWorker over real HTTP against a deterministic stand-in endpoint.

Covers: JSON-schema request with thinking disabled, retries on invalid output, cancellation
of an in-flight request, and that saved content survives a failed/cancelled run.
"""

import json
import socket
import threading
import time

import pytest
from fastapi import FastAPI, Request

from test_extraction import _setup


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def fake_ollama():
    import uvicorn

    state = {"mode": "ok", "requests": []}
    app = FastAPI()

    @app.get("/api/version")
    def version():
        return {"version": "0.0-test"}

    @app.get("/api/tags")
    def tags():
        return {"models": [{"name": "qwen3:4b", "size": 1, "details": {}}]}

    @app.post("/api/chat")
    async def chat(req: Request):
        body = await req.json()
        state["requests"].append(body)
        if state["mode"] == "slow":
            import asyncio

            await asyncio.sleep(5)
        if state["mode"] == "garbage":
            return {"message": {"role": "assistant", "content": "this is not json"}}
        content = {"items": [{"type": "bug", "title": "Sir Spin A Lot clips through the wall", "description": "Only this boss.",
                              "statement": "observation", "source_ids": ["S4", "S5"]}]}
        return {"message": {"role": "assistant", "content": json.dumps(content)}}

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    srv.install_signal_handlers = lambda: None
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}", state
    srv.should_exit = True
    t.join(5)


def _configure(url, **kw):
    from checkpoint.settings_store import update_settings

    update_settings({"ai": {"enabled": True, "base_url": url, "model": "qwen3:4b", "timeout_seconds": 10, "max_retries": 1, **kw}})


def _run_state(run_id):
    from checkpoint.db import read_session
    from checkpoint.models import ProcessingRun

    with read_session() as s:
        r = s.get(ProcessingRun, run_id)
        return r.state, r.error, r.stats


def test_worker_runs_and_requests_schema_without_thinking(app_env, fake_ollama):
    from checkpoint.extraction.worker import ExtractionWorker, queue_run
    from checkpoint.services import review as rv

    url, state = fake_ollama
    _configure(url)
    _pid, sid, *_ = _setup()
    rid = queue_run(sid)
    assert ExtractionWorker().step() is True
    st, err, stats = _run_state(rid)
    assert st == "done", err
    body = state["requests"][0]
    assert body["think"] is False and body["stream"] is False and body["format"]["type"] == "object"
    assert [d["title"] for d in rv.list_drafts(session_id=sid)] == ["Sir Spin A Lot clips through the wall"]
    # Same inputs again (not forced) → skipped as up to date, no extra model call.
    n = len(state["requests"])
    rid2 = queue_run(sid, force=False)
    ExtractionWorker().step()
    assert _run_state(rid2)[0] == "done" and len(state["requests"]) == n


def test_invalid_output_fails_with_message_and_keeps_content(app_env, fake_ollama):
    from checkpoint.extraction.worker import ExtractionWorker, queue_run
    from checkpoint.services import review as rv

    url, state = fake_ollama
    state["mode"] = "garbage"
    _configure(url)
    _pid, sid, *_ = _setup()
    rid = queue_run(sid)
    ExtractionWorker().step()
    st, err, _ = _run_state(rid)
    assert st == "failed" and "structure" in err
    assert len(state["requests"]) == 2  # bounded retries (max_retries=1)
    assert rv.list_drafts(session_id=sid) == []
    from checkpoint.db import read_session
    from checkpoint.models import TranscriptSegment

    with read_session() as s:
        assert s.query(TranscriptSegment).filter_by(session_id=sid).count() == 7  # nothing lost


def test_cancel_in_flight_request_then_recover(app_env, fake_ollama):
    from checkpoint.extraction.worker import ExtractionWorker, queue_run, recover_runs

    url, state = fake_ollama
    state["mode"] = "slow"
    _configure(url)
    _pid, sid, *_ = _setup()
    rid = queue_run(sid)
    w = ExtractionWorker()
    t = threading.Thread(target=w.step)
    t.start()
    time.sleep(1.0)
    t0 = time.monotonic()
    w.cancel_current(rid)
    t.join(10)
    assert time.monotonic() - t0 < 4  # the HTTP request was aborted, not waited out
    assert _run_state(rid)[0] == "cancelled"
    # A run left "running" by a crash is requeued on the next launch.
    from checkpoint.db import write_session
    from checkpoint.models import ProcessingRun

    state["mode"] = "ok"
    rid2 = queue_run(sid)
    with write_session() as s:
        s.get(ProcessingRun, rid2).state = "running"
    recover_runs()
    assert _run_state(rid2)[0] == "queued"
    ExtractionWorker().step()
    assert _run_state(rid2)[0] == "done"
