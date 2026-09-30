"""Send-to-AI: handoff bundle, MCP connector over stdio, and config connect/disconnect."""

import asyncio
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

import pytest


def _approved_demo_items():
    from checkpoint.services import demo, review as rv
    from checkpoint.services.capture import CaptureService
    from checkpoint.services.sessions import SessionManager

    demo.create_demo(CaptureService(SessionManager()))
    return [x["work_item_id"] for x in rv.approve([d["id"] for d in rv.list_drafts(state="pending")])]


def test_handoff_bundle_and_mcp_connector(app_env, monkeypatch):
    from checkpoint.services import handoff as ho

    wids = _approved_demo_items()
    h = ho.create(wids, True, True, "Fix these please")
    assert h["code"] == "H-1" and 'get_handoff (handoff "H-1")' in h["prompt"] and "Fix these please" in h["prompt"]
    b = ho.bundle_for(ho.resolve("latest"))
    assert b.images and all(p.suffix == ".jpg" and p.stat().st_size < 1_000_000 for _c, p in b.images)
    assert "Only the player aiming the cannon" in b.markdown and "possibly related" in b.markdown

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "checkpoint.mcp_server"],
                                       cwd=str(Path(__file__).resolve().parents[1]),
                                       env={**os.environ, "CHECKPOINT_DATA_DIR": str(app_env), "PYTHONIOENCODING": "utf-8"})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name: t for t in (await s.list_tools()).tools}
                assert set(tools) == {"get_handoff", "list_projects", "list_items", "get_item"}
                assert all(t.annotations.read_only_hint for t in tools.values())
                res = await s.call_tool("get_handoff", {"handoff": "H-1"})
                kinds = [c.type for c in res.content]
                assert not res.is_error and kinds[0] == "text" and kinds.count("image") == len(b.images)
                items = (await s.call_tool("list_items", {"type": "bug"})).content[0].text
                assert "Sir Spin A Lot" in items and "[Note]" not in items

    asyncio.run(run())
    from checkpoint.db import read_session
    from checkpoint.models import Handoff

    with read_session() as s:
        assert s.query(Handoff).one().fetched_at is not None


def test_connect_and_disconnect_on_copies_of_real_configs(tmp_path, monkeypatch):
    from checkpoint import integrations as it

    appdata, codex_home = tmp_path / "Roaming", tmp_path / ".codex"
    (appdata / "Claude").mkdir(parents=True)
    codex_home.mkdir()
    real_claude = Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    real_codex = Path.home() / ".codex" / "config.toml"
    if real_claude.is_file():
        shutil.copy(real_claude, appdata / "Claude" / "claude_desktop_config.json")
    else:
        (appdata / "Claude" / "claude_desktop_config.json").write_text('{"preferences": {"x": 1}}')
    if real_codex.is_file():
        shutil.copy(real_codex, codex_home / "config.toml")
    else:
        (codex_home / "config.toml").write_text('[mcp_servers.other]\ncommand = "x"\n')
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    claude_before = json.loads((appdata / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
    codex_before = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    # The real configs may already contain our entry (user connected); after Disconnect it must be gone
    # and everything else unchanged.
    claude_before.get("mcpServers", {}).pop("checkpoint", None)
    if not claude_before.get("mcpServers", True):
        claude_before.pop("mcpServers")
    codex_before.get("mcp_servers", {}).pop("checkpoint", None)
    if not codex_before.get("mcp_servers", True):
        codex_before.pop("mcp_servers")

    assert it.set_connected("claude", True)["connected"]
    assert it.set_connected("codex", True)["connected"]
    it.set_connected("codex", True)  # idempotent: no duplicate section
    cfg = json.loads((appdata / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
    assert cfg["mcpServers"]["checkpoint"]["args"] == ["-m", "checkpoint.mcp_server"]
    assert {k: v for k, v in cfg.items() if k != "mcpServers"} == {k: v for k, v in claude_before.items() if k != "mcpServers"}
    toml = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    assert Path(toml["mcp_servers"]["checkpoint"]["command"]).name == "python.exe"
    assert (codex_home / "config.toml.checkpoint-backup").is_file()

    it.set_connected("claude", False)
    it.set_connected("codex", False)
    assert json.loads((appdata / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8")) == claude_before
    assert tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8")) == codex_before


def test_prompt_wording_follows_item_types():
    from checkpoint.services.handoff import task_text

    bugs = task_text("implement_commit", "", {"bug"})
    assert bugs.startswith("Please fix each of these bugs") and "commit each fix" in bugs and "design decision" not in bugs
    feats = task_text("implement_commit", "", {"improvement", "task"})
    assert feats.startswith("Please implement each of these items") and "fix" not in feats.split("\n")[0] and "commit each change" in feats
    mixed = task_text("implement", "Code is in src/", {"bug", "idea"})
    assert "fix the bugs and implement" in mixed and "Don't commit" in mixed and mixed.endswith("Additional notes: Code is in src/")
    assert "Don't change any files" in task_text("investigate", "", {"bug"})
    assert task_text("read", "", {"bug"}) == ""
    assert task_text("fix_commit", "", {"bug"}) == bugs  # older saved choice still works
