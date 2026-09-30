"""One-click connection of the Checkpoint MCP connector to Claude Desktop and Codex.

Only runs when the user presses Connect/Disconnect. Each config file is backed up next to
itself (*.checkpoint-backup) before it is changed, other settings are preserved, and the
result is re-read to make sure the file is still valid.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

SERVER_KEY = "checkpoint"
BACKEND_DIR = Path(__file__).resolve().parents[1]
BEGIN, END = "# >>> checkpoint connector (managed by Checkpoint)", "# <<< checkpoint connector"


class IntegrationError(Exception):
    pass


def server_command() -> dict:
    exe = Path(sys.executable)
    # pythonw has no stdio; assistants need the console interpreter.
    if exe.name.lower() == "pythonw.exe":
        exe = exe.with_name("python.exe")
    return {"command": str(exe), "args": ["-m", "checkpoint.mcp_server"],
            "env": {"PYTHONPATH": str(BACKEND_DIR), "PYTHONIOENCODING": "utf-8"}}


def _backup(p: Path) -> None:
    if p.is_file():
        shutil.copy2(p, p.with_name(p.name + ".checkpoint-backup"))


# ------------------------------------------------------------------ Claude Desktop
def claude_config_paths() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", ""))
    paths = [appdata / "Claude" / "claude_desktop_config.json"]
    pkgs = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
    if pkgs.is_dir():  # Microsoft Store build keeps a per-package copy
        paths += [p / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json" for p in pkgs.glob("Claude_*")]
    return paths


def _claude_existing() -> list[Path]:
    return [p for p in claude_config_paths() if p.is_file()]


def claude_status() -> dict:
    files = _claude_existing()
    installed = bool(files) or (Path(os.environ.get("APPDATA", "")) / "Claude").is_dir()
    connected = False
    for p in files:
        try:
            connected |= SERVER_KEY in (json.loads(p.read_text(encoding="utf-8")).get("mcpServers") or {})
        except (OSError, ValueError):
            pass
    return {"app": "claude", "name": "Claude Desktop", "installed": installed, "connected": connected,
            "config": str(files[0]) if files else str(claude_config_paths()[0])}


def claude_set(connect: bool) -> dict:
    files = _claude_existing() or ([claude_config_paths()[0]] if connect else [])
    if not files and connect:
        raise IntegrationError("Claude Desktop doesn't seem to be installed.")
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except ValueError as e:
            raise IntegrationError(f"Claude Desktop's config isn't valid JSON, so it wasn't changed ({p}): {e}") from e
        servers = dict(data.get("mcpServers") or {})
        if connect:
            servers[SERVER_KEY] = server_command()
        else:
            servers.pop(SERVER_KEY, None)
        if servers:
            data["mcpServers"] = servers
        else:
            data.pop("mcpServers", None)
        p.parent.mkdir(parents=True, exist_ok=True)
        _backup(p)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        json.loads(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, p)
    return claude_status()


# ------------------------------------------------------------------ Codex
def codex_config_path() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return home / "config.toml"


def _toml_str(s: str) -> str:
    return "'" + s + "'" if "'" not in s else json.dumps(s)


def _codex_block() -> str:
    c = server_command()
    args = ", ".join(json.dumps(a) for a in c["args"])
    env = "\n".join(f"{k} = {_toml_str(v)}" for k, v in c["env"].items())
    return (f"{BEGIN}\n[mcp_servers.{SERVER_KEY}]\ncommand = {_toml_str(c['command'])}\nargs = [{args}]\n\n"
            f"[mcp_servers.{SERVER_KEY}.env]\n{env}\n{END}\n")


def _strip_block(text: str) -> str:
    if BEGIN not in text:
        return text
    head, rest = text.split(BEGIN, 1)
    tail = rest.split(END, 1)[1] if END in rest else ""
    return head.rstrip() + "\n" + tail.lstrip("\n")


def codex_status() -> dict:
    p = codex_config_path()
    connected = False
    if p.is_file():
        try:
            connected = SERVER_KEY in (tomllib.loads(p.read_text(encoding="utf-8")).get("mcp_servers") or {})
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return {"app": "codex", "name": "Codex", "installed": p.parent.is_dir(), "connected": connected, "config": str(p)}


def codex_set(connect: bool) -> dict:
    p = codex_config_path()
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    try:
        existing = tomllib.loads(text) if text else {}
    except tomllib.TOMLDecodeError as e:
        raise IntegrationError(f"Codex's config.toml isn't valid, so it wasn't changed: {e}") from e
    ours = BEGIN in text
    if connect and SERVER_KEY in (existing.get("mcp_servers") or {}) and not ours:
        raise IntegrationError("Codex already has an MCP server named “checkpoint” that Checkpoint didn't add. Remove it first.")
    new = _strip_block(text)
    if connect:
        new = new.rstrip() + ("\n\n" if new.strip() else "") + _codex_block()
    tomllib.loads(new)  # never write an invalid file
    p.parent.mkdir(parents=True, exist_ok=True)
    _backup(p)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, p)
    return codex_status()


def status() -> list[dict]:
    return [claude_status(), codex_status()]


def set_connected(app: str, connect: bool) -> dict:
    if app == "claude":
        return claude_set(connect)
    if app == "codex":
        return codex_set(connect)
    raise IntegrationError("Unknown app")
