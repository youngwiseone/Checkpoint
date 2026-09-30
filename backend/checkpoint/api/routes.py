"""HTTP routes for the local UI."""

from __future__ import annotations

import io
import struct
import wave
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import __version__
from ..config import paths, set_data_dir_pointer
from ..db import read_session, write_session
from ..events import hooks, notices
from ..models import (
    AudioChunk,
    AudioEvent,
    AudioSource,
    Capture,
    DraftItem,
    Note,
    ProcessingRun,
    Project,
    Session,
    SessionPause,
    SyncJob,
    TranscriptionJob,
    TranscriptSegment,
    WorkItem,
)
from ..services import review as rv
from ..services.capture import capture_context_state
from ..services.sessions import SourceConfig, StartSessionRequest
from ..settings_store import get_settings, update_settings
from ..storage import StorageError, audio_path, media_path

router = APIRouter(prefix="/api")


def core():  # noqa: ANN201
    from ..core import get_core

    return get_core()


def _bad(e: Exception, code: int = 400) -> HTTPException:
    return HTTPException(code, str(e))


def _iso(d):  # noqa: ANN001, ANN202
    return d.isoformat() if d else None


# ================================================================== system
@router.get("/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@router.get("/auth/status")
def auth_status(request: Request) -> dict:
    from .security import COOKIE

    auth = request.app.state.auth
    return {"authenticated": auth.cookie_ok(request.cookies.get(COOKIE))}


@router.get("/state")
def state(after: int = 0) -> dict:
    c = core()
    st = get_settings()
    return {
        "version": __version__,
        "first_run_complete": st.first_run_complete,
        "session": c.sessions.status(),
        "review": rv.review_counts(),
        "workers": c.worker_status(),
        "desktop": {"available": hooks.desktop_available, "hotkeys": hooks.hotkey_status()},
        "notices": notices.since(after),
        "ai_enabled": st.ai.enabled,
    }


@router.get("/readiness")
def readiness() -> dict:
    from ..audio.devices import audio_available, list_devices
    from ..capture.screen import list_monitors
    from ..extraction.ollama import OllamaProvider, ProviderError
    from ..sharing.client import load_token
    from ..transcription.engine import model_installed

    st = get_settings()
    checks = []
    mons = list_monitors()
    hk = hooks.hotkey_status()
    if mons and hooks.desktop_available and hk.get("ok"):
        checks.append({"id": "screenshot", "label": "Screenshot capture", "state": "ready",
                       "detail": f"{len(mons)} display(s) · hotkeys {st.hotkeys.capture}, {st.hotkeys.capture_context}, {st.hotkeys.quick_note}"})
    elif mons and hooks.desktop_available:
        checks.append({"id": "screenshot", "label": "Screenshot capture", "state": "warning",
                       "detail": hk.get("reason") or "Some hotkeys couldn't be registered.",
                       "remedy": "Another app owns the shortcut. Pick different keys in Settings → Shortcuts."})
    elif mons:
        checks.append({"id": "screenshot", "label": "Screenshot capture", "state": "warning",
                       "detail": "Desktop helper isn't running, so global hotkeys are unavailable.",
                       "remedy": "Launch with start.cmd to get the tray icon and hotkeys."})
    else:
        checks.append({"id": "screenshot", "label": "Screenshot capture", "state": "error", "detail": "No displays detected."})
    ok, why = audio_available()
    devs = list_devices() if ok else {"mic": [], "loopback": []}
    checks.append({"id": "mic", "label": "Microphone", "state": "ready" if devs["mic"] else "unavailable",
                   "detail": f"{len(devs['mic'])} input device(s)" if devs["mic"] else (why or "No microphone found"),
                   "remedy": None if devs["mic"] else "Optional. Connect a microphone and check Windows privacy settings (Microphone access)."})
    checks.append({"id": "loopback", "label": "Computer audio", "state": "ready" if devs["loopback"] else "unavailable",
                   "detail": f"{len(devs['loopback'])} output device(s) can be recorded" if devs["loopback"] else (why or "No output devices found"),
                   "remedy": None if devs["loopback"] else "Optional. Requires a WASAPI output device."})
    m = st.transcription.model
    checks.append({"id": "transcription", "label": "Transcription model", "state": "ready" if model_installed(m) else "unavailable",
                   "detail": f"{m} installed" if model_installed(m) else f"{m} not downloaded",
                   "remedy": None if model_installed(m) else "Optional. Download it in Settings → Transcription (one-time, needs internet)."})
    if st.ai.enabled:
        from ..extraction.ollama import ensure_running

        ensure_running(st.ai.base_url, wait_s=10)
    prov = OllamaProvider(st.ai.base_url)
    ver = prov.version()
    if not ver:
        checks.append({"id": "ai", "label": "Local AI (Ollama)", "state": "unavailable", "detail": "Ollama isn't running",
                       "remedy": "Optional. Install Ollama from ollama.com, start it, then pull a model in Settings → Local AI."})
    else:
        try:
            names = [x["name"] for x in prov.list_models()]
        except ProviderError:
            names = []
        has = any(n == st.ai.model or n.split(":")[0] == st.ai.model for n in names)
        checks.append({"id": "ai", "label": "Local AI (Ollama)", "state": "ready" if has and st.ai.enabled else ("warning" if has else "unavailable"),
                       "detail": f"Ollama {ver} · {st.ai.model} {'installed' if has else 'not installed'}" + ("" if st.ai.enabled else " · turned off"),
                       "remedy": None if has else f"Pull {st.ai.model} in Settings → Local AI."})
    if st.sharing.server_url:
        tok = load_token(st.sharing.server_url)
        checks.append({"id": "sharing", "label": "Shared connection", "state": "ready" if tok else "warning",
                       "detail": st.sharing.server_url, "remedy": None if tok else "Save the workspace token in Settings → Sharing."})
    else:
        checks.append({"id": "sharing", "label": "Shared connection", "state": "unavailable", "detail": "Not configured",
                       "remedy": "Optional. Everything works locally without a server."})
    return {"checks": checks}


@router.get("/notices")
def get_notices(after: int = 0) -> list[dict]:
    return notices.since(after)


# ================================================================== settings
@router.get("/settings")
def get_settings_route() -> dict:
    s = get_settings().model_dump()
    s["data_dir"] = str(paths().root)
    return s


@router.patch("/settings")
def patch_settings(patch: dict[str, Any]) -> dict:
    patch.pop("data_dir", None)
    try:
        s = update_settings(patch)
    except Exception as e:  # noqa: BLE001
        raise _bad(e) from e
    result = {"settings": s.model_dump()}
    if "hotkeys" in patch and hooks.rebind_hotkeys:
        result["hotkeys"] = hooks.rebind_hotkeys()
    if "transcription" in patch:
        core().transcriber.wake()
    return result


class DataDirIn(BaseModel):
    path: str


@router.post("/settings/data-dir")
def set_data_dir(body: DataDirIn) -> dict:
    from pathlib import Path

    p = Path(body.path).expanduser()
    if not p.is_absolute():
        raise HTTPException(400, "Use an absolute folder path")
    try:
        p.mkdir(parents=True, exist_ok=True)
        (p / ".write-test").write_text("ok")
        (p / ".write-test").unlink()
    except OSError as e:
        raise HTTPException(400, f"Can't write to that folder: {e}") from e
    set_data_dir_pointer(p)
    return {"ok": True, "restart_required": True,
            "message": "Saved. Quit and restart Checkpoint to use the new location. Existing data is not moved automatically."}


@router.get("/storage")
def storage() -> dict:
    from ..services.retention import usage

    return usage()


# ================================================================== projects
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    glossary: str = ""


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    glossary: Optional[str] = None
    archived: Optional[bool] = None
    repo_path: Optional[str] = Field(default=None, max_length=1000)
    base_branch: Optional[str] = Field(default=None, max_length=200)
    default_agent: Optional[str] = None
    agent_access: Optional[str] = None
    setup_command: Optional[str] = Field(default=None, max_length=2000)
    check_command: Optional[str] = Field(default=None, max_length=2000)
    preview_command: Optional[str] = Field(default=None, max_length=2000)
    preview_url: Optional[str] = Field(default=None, max_length=500)


def project_dict(s, p: Project) -> dict:  # noqa: ANN001
    sessions = s.scalar(select(func.count()).select_from(Session).where(Session.project_id == p.id)) or 0
    open_items = s.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.project_id == p.id, WorkItem.work_status.in_(("open", "in_progress")))) or 0
    pending = s.scalar(select(func.count()).select_from(DraftItem).where(DraftItem.project_id == p.id, DraftItem.review_state == "pending", DraftItem.merged_into_id.is_(None))) or 0
    return {"id": p.id, "name": p.name, "description": p.description, "glossary": p.glossary, "is_demo": p.is_demo,
            "archived": p.archived, "shared_project_id": p.shared_project_id, "shared_project_name": p.shared_project_name,
            "shared_server_url": p.shared_server_url, "last_refreshed_at": _iso(p.last_refreshed_at),
            "session_count": sessions, "open_items": open_items, "pending_cards": pending, "created_at": _iso(p.created_at),
            "repo_path": p.repo_path, "base_branch": p.base_branch, "default_agent": p.default_agent,
            "agent_access": p.agent_access, "setup_command": p.setup_command, "check_command": p.check_command,
            "preview_command": p.preview_command, "preview_url": p.preview_url,
            "configured": bool(p.repo_path and p.default_agent in ("claude", "codex"))}


@router.get("/projects")
def list_projects() -> list[dict]:
    with read_session() as s:
        return [project_dict(s, p) for p in s.scalars(select(Project).order_by(Project.is_demo, Project.name)).all()]


@router.post("/projects")
def create_project(body: ProjectIn) -> dict:
    with write_session() as s:
        p = Project(name=body.name.strip(), description=body.description.strip(), glossary=body.glossary.strip())
        s.add(p)
        s.flush()
        return project_dict(s, p)


@router.patch("/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch) -> dict:
    with write_session() as s:
        p = s.get(Project, pid)
        if p is None:
            raise HTTPException(404, "Project not found")
        patch = body.model_dump(exclude_none=True)
        if patch.get("default_agent") not in (None, "none", "claude", "codex"):
            raise HTTPException(400, "Unknown agent")
        if patch.get("agent_access") not in (None, "standard", "full"):
            raise HTTPException(400, "Unknown access level")
        if patch.get("repo_path"):
            from ..services.workspace import GitError, inspect_repo

            try:
                patch["repo_path"] = inspect_repo(patch["repo_path"]).root
            except GitError as e:
                raise HTTPException(400, str(e)) from e
            if patch["repo_path"] != p.repo_path and "base_branch" not in patch:
                patch["base_branch"] = ""  # a branch of the old repo means nothing in the new one
        for k, v in patch.items():
            setattr(p, k, (v.strip() or None) if k in ("repo_path", "base_branch") and isinstance(v, str)
                    else (v.strip() if isinstance(v, str) else v))
        if not p.name:
            raise HTTPException(400, "Name can't be empty")
        s.flush()
        return project_dict(s, p)


@router.post("/demo")
def create_demo() -> dict:
    from ..services.demo import create_demo as mk

    return {"project_id": mk(core().capture)}


@router.delete("/demo")
def delete_demo() -> dict:
    from ..services.demo import remove_demo

    return {"removed": remove_demo()}


# ================================================================== sessions
def session_dict(s, x: Session) -> dict:  # noqa: ANN001
    caps = s.scalar(select(func.count()).select_from(Capture).where(Capture.session_id == x.id, Capture.status != "discarded")) or 0
    pending = s.scalar(select(func.count()).select_from(DraftItem).where(DraftItem.session_id == x.id, DraftItem.review_state == "pending", DraftItem.merged_into_id.is_(None))) or 0
    cards = s.scalar(select(func.count()).select_from(DraftItem).where(DraftItem.session_id == x.id, DraftItem.merged_into_id.is_(None))) or 0
    jobs = dict(s.execute(select(TranscriptionJob.state, func.count()).where(TranscriptionJob.session_id == x.id).group_by(TranscriptionJob.state)).all())
    srcs = s.scalars(select(AudioSource).where(AudioSource.session_id == x.id)).all()
    return {
        "id": x.id, "project_id": x.project_id, "title": x.title, "purpose": x.purpose, "state": x.state,
        "started_at": _iso(x.started_at), "ended_at": _iso(x.ended_at), "last_heartbeat_at": _iso(x.last_heartbeat_at),
        "transcription_mode": x.transcription_mode, "ai_enabled": x.ai_enabled, "processing_state": x.processing_state,
        "capture_count": caps, "pending_cards": pending, "card_count": cards, "is_demo": x.is_demo,
        "raw_audio_deleted": x.raw_audio_deleted, "capture_target": x.capture_target,
        "sources": [{"id": a.id, "kind": a.kind, "label": a.label, "device": a.device_name, "state": a.state, "last_error": a.last_error} for a in srcs],
        "transcription": {k: jobs.get(k, 0) for k in ("queued", "running", "done", "failed")},
    }


@router.get("/sessions")
def list_sessions(project_id: Optional[str] = None) -> list[dict]:
    with read_session() as s:
        q = select(Session).order_by(Session.started_at.desc())
        if project_id:
            q = q.where(Session.project_id == project_id)
        return [session_dict(s, x) for x in s.scalars(q).all()]


@router.get("/sessions/{sid}")
def get_session(sid: str) -> dict:
    from ..extraction.worker import run_dict

    with read_session() as s:
        x = s.get(Session, sid)
        if x is None:
            raise HTTPException(404, "Session not found")
        d = session_dict(s, x)
        d["pauses"] = [{"start_ms": p.start_offset_ms, "end_ms": p.end_offset_ms, "reason": p.reason}
                       for p in s.scalars(select(SessionPause).where(SessionPause.session_id == sid).order_by(SessionPause.start_offset_ms)).all()]
        src_ids = [a["id"] for a in d["sources"]]
        evs = s.scalars(select(AudioEvent).where(AudioEvent.source_id.in_(src_ids)).order_by(AudioEvent.offset_ms)).all() if src_ids else []
        d["audio_events"] = [{"source_id": e.source_id, "kind": e.kind, "offset_ms": e.offset_ms, "message": e.message} for e in evs]
        run = s.scalars(select(ProcessingRun).where(ProcessingRun.session_id == sid).order_by(ProcessingRun.created_at.desc())).first()
        d["latest_run"] = run_dict(run) if run else None
        failed = s.scalars(select(TranscriptionJob).where(TranscriptionJob.session_id == sid, TranscriptionJob.state == "failed")).all()
        d["transcription_errors"] = list({j.error for j in failed if j.error})[:3]
        proj = s.get(Project, x.project_id)
        d["project_name"] = proj.name if proj else ""
        d["audio_seconds"] = (s.scalar(select(func.sum(AudioChunk.duration_ms)).where(AudioChunk.session_id == sid)) or 0) / 1000
        return d


@router.post("/sessions")
def start_session(req: StartSessionRequest) -> dict:
    try:
        sid = core().sessions.start(req)
    except ValueError as e:
        raise _bad(e) from e
    return {"session_id": sid}


@router.post("/sessions/active/{action}")
def session_action(action: str) -> dict:
    sm = core().sessions
    if action == "pause":
        sm.pause()
    elif action == "resume":
        sm.resume()
    elif action == "end":
        sid = sm.end()
        core().transcriber.wake()
        core().organiser.wake()
        return {"session_id": sid}
    else:
        raise HTTPException(404, "Unknown action")
    return sm.status()


class QuickStartIn(BaseModel):
    project_id: Optional[str] = None


@router.post("/sessions/quick")
def quick_start(body: QuickStartIn) -> dict:
    try:
        sid = core().quick_start(body.project_id)
    except ValueError as e:
        raise _bad(e) from e
    return {"session_id": sid}


@router.get("/projects/{pid}/setup")
def project_setup(pid: str) -> dict:
    """What a quick start for this project would record (its last session's choices)."""
    from ..services.sessions import remembered_setup

    return remembered_setup(pid).model_dump()


class SourceToggle(BaseModel):
    enabled: bool
    device: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=100)


class ActivePatch(BaseModel):
    mic: Optional[SourceToggle] = None
    loopback: Optional[SourceToggle] = None
    transcription_mode: Optional[str] = None
    always_ask_context: Optional[bool] = None
    capture_target: Optional[str] = None
    auto_capture: Optional[bool] = None


@router.patch("/sessions/active")
def patch_active(body: ActivePatch) -> dict:
    """Change what is being recorded while the session runs."""
    sm = core().sessions
    a = sm.active
    if a is None:
        raise HTTPException(409, "No session is running")
    try:
        for kind in ("mic", "loopback"):
            t = getattr(body, kind)
            if t is not None:
                sm.set_source(kind, t.enabled, t.device, t.label)
        if body.transcription_mode:
            sm.set_transcription_mode(a.id, body.transcription_mode)
            core().transcriber.wake()
        if body.always_ask_context is not None or body.capture_target:
            sm.set_options(body.always_ask_context, body.capture_target)
    except ValueError as e:
        raise _bad(e) from e
    if body.auto_capture is not None:
        update_settings({"auto_capture": {"enabled": body.auto_capture}})
        core().autocapture.wake()
    return sm.status()


@router.get("/running-apps")
def running_apps() -> list[dict]:
    """Programs with a visible window, for picking which one starts a project's session."""
    from ..capture.win32 import visible_windows

    seen, out = set(), []
    for w in visible_windows():
        key = (w["exe"].lower(), w["title"])
        if w["exe"] and key not in seen:
            seen.add(key)
            out.append({"exe": w["exe"], "title": w["title"]})
    return sorted(out, key=lambda w: (w["exe"].lower(), w["title"].lower()))


@router.post("/offer/dismiss")
def dismiss_offer() -> dict:
    core().appwatch.dismiss()
    return {"ok": True}


@router.post("/sessions/active/sources/{kind}/retry")
def retry_source(kind: str) -> dict:
    core().sessions.retry_source(kind)
    return core().sessions.status()


class ResumeIn(BaseModel):
    mic: SourceConfig = Field(default_factory=SourceConfig)
    loopback: SourceConfig = Field(default_factory=SourceConfig)


@router.post("/sessions/{sid}/resume")
def resume_interrupted(sid: str, body: ResumeIn) -> dict:
    try:
        req = StartSessionRequest(project_id="-", mic=body.mic, loopback=body.loopback)
        core().sessions.resume_interrupted(sid, req)
    except ValueError as e:
        raise _bad(e) from e
    return {"session_id": sid}


@router.post("/sessions/{sid}/finish")
def finish_interrupted(sid: str) -> dict:
    try:
        core().sessions.end_interrupted(sid)
    except ValueError as e:
        raise _bad(e) from e
    core().transcriber.wake()
    return {"ok": True}


class SessionPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    purpose: Optional[str] = None
    transcription_mode: Optional[str] = None


@router.patch("/sessions/{sid}")
def patch_session(sid: str, body: SessionPatch) -> dict:
    if body.transcription_mode:
        try:
            core().sessions.set_transcription_mode(sid, body.transcription_mode)
        except ValueError as e:
            raise _bad(e) from e
        core().transcriber.wake()
    with write_session() as s:
        x = s.get(Session, sid)
        if x is None:
            raise HTTPException(404, "Session not found")
        if body.title is not None and body.title.strip() and body.title.strip() != x.title:
            if x.name_locked:
                raise HTTPException(409, f"The name is locked to branch {x.branch} since the first send.")
            x.title = body.title.strip()
            x.name_edited = True
        if body.purpose is not None:
            x.purpose = body.purpose
    return get_session(sid)


@router.post("/sessions/{sid}/transcribe")
def transcribe_now(sid: str) -> dict:
    with write_session() as s:
        x = s.get(Session, sid)
        if x is None:
            raise HTTPException(404, "Session not found")
        if x.transcription_mode == "off":
            x.transcription_mode = "after" if x.state in ("ended", "interrupted") else "live"
        for j in s.scalars(select(TranscriptionJob).where(TranscriptionJob.session_id == sid, TranscriptionJob.state == "failed")).all():
            j.state = "queued"
            j.attempts = 0
    core().transcriber.wake()
    return {"ok": True}


@router.get("/sessions/{sid}/timeline")
def timeline(sid: str) -> dict:
    st = get_settings().ai
    with read_session() as s:
        x = s.get(Session, sid)
        if x is None:
            raise HTTPException(404, "Session not found")
        labels = {a.id: (a.label or ("Microphone" if a.kind == "mic" else "Computer audio"), a.kind)
                  for a in s.scalars(select(AudioSource).where(AudioSource.session_id == sid)).all()}
        out = []
        for seg in s.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == sid).order_by(TranscriptSegment.start_ms)).all():
            lab = labels.get(seg.source_id, ("", ""))
            out.append({"kind": "segment", "id": seg.id, "offset_ms": seg.start_ms, "end_ms": seg.end_ms, "text": seg.corrected_text or seg.text,
                        "raw_text": seg.text, "corrected": seg.corrected_text is not None, "source_label": lab[0], "source_kind": lab[1],
                        "low_confidence": seg.low_confidence, "audio_url": f"/api/segments/{seg.id}/audio" if seg.chunk_id and not x.raw_audio_deleted else None})
        for n in s.scalars(select(Note).where(Note.session_id == sid)).all():
            out.append({"kind": "note", "id": n.id, "offset_ms": n.offset_ms, "text": n.text, "note_kind": n.kind, "capture_id": n.capture_id,
                        "category": n.category})
        for c in s.scalars(select(Capture).where(Capture.session_id == sid, Capture.status != "discarded")).all():
            out.append({"kind": "capture", "id": c.id, "offset_ms": c.offset_ms, "status": c.status, "window_title": c.window_title,
                        "trigger": c.trigger, "reason": c.reason,
                        "thumb_url": f"/api/media/captures/{c.id}/thumb", "image_url": f"/api/media/captures/{c.id}/image",
                        "context_state": capture_context_state(x, c, st.window_before_s, st.window_after_s, s)})
        for p in s.scalars(select(SessionPause).where(SessionPause.session_id == sid)).all():
            out.append({"kind": "pause", "id": p.id, "offset_ms": p.start_offset_ms, "end_ms": p.end_offset_ms, "reason": p.reason})
        src_ids = list(labels)
        if src_ids:
            for e in s.scalars(select(AudioEvent).where(AudioEvent.source_id.in_(src_ids), AudioEvent.kind.in_(("interrupted", "resumed", "error", "lost_on_crash")))).all():
                out.append({"kind": "audio_event", "id": e.id, "offset_ms": e.offset_ms, "event": e.kind, "message": e.message,
                            "source_label": labels.get(e.source_id, ("", ""))[0]})
    out.sort(key=lambda i: (i["offset_ms"] if i["offset_ms"] is not None else 0))
    return {"items": out}


@router.get("/sessions/{sid}/recap")
def get_recap(sid: str) -> dict:
    try:
        return rv.recap(sid)
    except rv.ReviewError as e:
        raise _bad(e, 404) from e


class OrganiseIn(BaseModel):
    force: bool = True


@router.post("/sessions/{sid}/organise")
def organise(sid: str, body: OrganiseIn) -> dict:
    from ..extraction.worker import queue_run

    try:
        rid = queue_run(sid, force=body.force)
    except ValueError as e:
        raise _bad(e) from e
    core().organiser.wake()
    return {"run_id": rid}


@router.post("/runs/{rid}/cancel")
def cancel_run(rid: str) -> dict:
    core().organiser.cancel_current(rid)
    return {"ok": True}


@router.get("/sessions/{sid}/impact")
def impact(sid: str) -> dict:
    from ..services.retention import session_impact

    try:
        return session_impact(sid)
    except ValueError as e:
        raise _bad(e, 404) from e


@router.post("/sessions/{sid}/delete-audio")
def delete_audio(sid: str) -> dict:
    from ..services.retention import delete_raw_audio

    try:
        return delete_raw_audio(sid)
    except ValueError as e:
        raise _bad(e) from e


@router.delete("/sessions/{sid}")
def delete_session(sid: str, confirm: bool = False) -> dict:
    from ..services.retention import delete_session as dele

    try:
        return dele(sid, confirm)
    except ValueError as e:
        raise _bad(e, 409) from e


# ---------------------------------------------------------------- checklist
class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ChecklistPatch(BaseModel):
    state: Optional[str] = None
    dismiss_suggestion: bool = False


class IdsIn(BaseModel):
    ids: list[str]


@router.get("/sessions/{sid}/checklist")
def get_checklist(sid: str) -> list[dict]:
    return rv.list_checklist(sid)


@router.post("/sessions/{sid}/checklist")
def add_checklist(sid: str, body: TextIn) -> dict:
    try:
        return rv.add_checklist(sid, body.text)
    except rv.ReviewError as e:
        raise _bad(e) from e


@router.post("/sessions/{sid}/checklist/bring-in")
def bring_in(sid: str, body: IdsIn) -> list[dict]:
    try:
        return rv.bring_in(sid, body.ids)
    except rv.ReviewError as e:
        raise _bad(e) from e


@router.post("/sessions/{sid}/keep-outstanding")
def keep_outstanding(sid: str) -> dict:
    try:
        return {"created": rv.keep_outstanding(sid)}
    except rv.ReviewError as e:
        raise _bad(e) from e


@router.patch("/checklist/{eid}")
def patch_checklist(eid: str, body: ChecklistPatch) -> dict:
    try:
        if body.dismiss_suggestion:
            return rv.dismiss_suggestion(eid)
        if body.state:
            return rv.set_checklist_state(eid, body.state)
    except rv.ReviewError as e:
        raise _bad(e) from e
    raise HTTPException(400, "Nothing to change")


@router.delete("/checklist/{eid}")
def delete_checklist(eid: str) -> dict:
    rv.delete_checklist(eid)
    return {"ok": True}


# ================================================================== captures & notes
class NoteIn(BaseModel):
    text: str = Field(max_length=20000)
    category: Optional[str] = None


def capture_dict(c: Capture) -> dict:
    return {"id": c.id, "session_id": c.session_id, "project_id": c.project_id, "offset_ms": c.offset_ms, "taken_at": _iso(c.taken_at),
            "status": c.status, "trigger": c.trigger, "reason": c.reason, "window_title": c.window_title, "width": c.width, "height": c.height,
            "thumb_url": f"/api/media/captures/{c.id}/thumb", "image_url": f"/api/media/captures/{c.id}/image"}


@router.get("/captures/unfinished")
def unfinished(project_id: Optional[str] = None) -> list[dict]:
    with read_session() as s:
        q = select(Capture).where(Capture.status == "unfinished").order_by(Capture.taken_at.desc())
        if project_id:
            q = q.where(Capture.project_id == project_id)
        return [capture_dict(c) for c in s.scalars(q).all()]


@router.get("/captures/unlinked")
def unlinked(session_id: str) -> list[dict]:
    """Audio-marker screenshots that no card uses yet."""
    from ..models import EvidenceLink

    st = get_settings().ai
    with read_session() as s:
        sess = s.get(Session, session_id)
        linked = set(s.scalars(select(EvidenceLink.capture_id).where(EvidenceLink.capture_id.is_not(None), EvidenceLink.draft_id.is_not(None))).all())
        out = []
        for c in s.scalars(select(Capture).where(Capture.session_id == session_id, Capture.status == "marker")).all():
            if c.id in linked:
                continue
            d = capture_dict(c)
            d["context_state"] = capture_context_state(sess, c, st.window_before_s, st.window_after_s, s)
            lo, hi = (c.offset_ms or 0) - st.window_before_s * 1000, (c.offset_ms or 0) + st.window_after_s * 1000
            segs = s.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == session_id, TranscriptSegment.end_ms >= lo,
                                                             TranscriptSegment.start_ms <= hi).order_by(TranscriptSegment.start_ms)).all()
            d["nearby"] = [{"id": g.id, "offset_ms": g.start_ms, "text": g.corrected_text or g.text} for g in segs]
            out.append(d)
        return out


@router.post("/captures/{cid}/note")
def capture_note(cid: str, body: NoteIn) -> dict:
    try:
        return core().capture.save_capture_note(cid, body.text, body.category)
    except ValueError as e:
        raise _bad(e) from e


@router.post("/captures/{cid}/discard")
def capture_discard(cid: str) -> dict:
    try:
        core().capture.discard(cid)
    except ValueError as e:
        raise _bad(e, 409) from e
    return {"ok": True}


@router.post("/notes/quick")
def quick_note(body: NoteIn) -> dict:
    try:
        return core().capture.quick_note(body.text, body.category)
    except ValueError as e:
        raise _bad(e) from e


@router.get("/media/captures/{cid}/{variant}")
def media(cid: str, variant: str) -> FileResponse:
    if variant not in ("image", "thumb"):
        raise HTTPException(404)
    with read_session() as s:
        c = s.get(Capture, cid)
        if c is None:
            raise HTTPException(404, "Not found")
        rel = c.image_rel_path if variant == "image" else c.thumb_rel_path
    try:
        p = media_path(rel)
    except StorageError as e:
        raise HTTPException(404) from e
    if not p.is_file():
        raise HTTPException(404, "Screenshot file missing")
    return FileResponse(p, media_type="image/png" if variant == "image" else "image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


# ================================================================== transcript segments
class SegmentPatch(BaseModel):
    corrected_text: Optional[str] = Field(default=None, max_length=5000)


@router.patch("/segments/{seg_id}")
def patch_segment(seg_id: str, body: SegmentPatch) -> dict:
    with write_session() as s:
        seg = s.get(TranscriptSegment, seg_id)
        if seg is None:
            raise HTTPException(404, "Segment not found")
        t = (body.corrected_text or "").strip()
        seg.corrected_text = t if t and t != seg.text else None
        return {"id": seg.id, "text": seg.corrected_text or seg.text, "raw_text": seg.text, "corrected": seg.corrected_text is not None}


@router.get("/segments/{seg_id}/audio")
def segment_audio(seg_id: str, pad_ms: int = 700) -> Response:
    with read_session() as s:
        seg = s.get(TranscriptSegment, seg_id)
        if seg is None or seg.chunk_id is None:
            raise HTTPException(404, "No audio for this segment")
        ch = s.get(AudioChunk, seg.chunk_id)
        if ch is None or ch.state in ("deleted", "missing"):
            raise HTTPException(410, "The raw audio for this segment was deleted")
    try:
        p = audio_path(ch.rel_path)
    except StorageError as e:
        raise HTTPException(404) from e
    if not p.is_file():
        raise HTTPException(410, "The audio file is missing")
    with wave.open(str(p), "rb") as w:
        rate, chans, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        start = max(0, int((seg.start_ms - ch.start_offset_ms - pad_ms) * rate / 1000))
        end = min(w.getnframes(), int((seg.end_ms - ch.start_offset_ms + pad_ms) * rate / 1000))
        w.setpos(min(start, w.getnframes()))
        frames = w.readframes(max(0, end - start))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(chans)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames)
    return Response(buf.getvalue(), media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})


# ================================================================== drafts (review cards)
class DraftPatch(BaseModel):
    type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    needs_context: Optional[bool] = None
    expected_version: Optional[int] = None


class DraftCreate(BaseModel):
    project_id: str
    session_id: Optional[str] = None
    type: str = "note"
    title: str = ""
    description: str = ""
    segment_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    capture_ids: list[str] = Field(default_factory=list)
    origin: str = "manual"


class MergeIn(BaseModel):
    ids: list[str]
    title: Optional[str] = None


class SplitIn(BaseModel):
    parts: list[dict]


class AttachIn(BaseModel):
    capture_id: str


@router.get("/drafts")
def drafts(project_id: Optional[str] = None, session_id: Optional[str] = None, state: Optional[str] = None) -> list[dict]:
    return rv.list_drafts(project_id, session_id, state)


@router.get("/review/counts")
def counts(project_id: Optional[str] = None) -> dict:
    return rv.review_counts(project_id)


@router.get("/drafts/{did}")
def draft(did: str) -> dict:
    try:
        return rv.get_draft(did)
    except rv.ReviewError as e:
        raise _bad(e, 404) from e


@router.patch("/drafts/{did}")
def patch_draft(did: str, body: DraftPatch) -> dict:
    try:
        return rv.update_draft(did, body.model_dump(exclude_none=True, exclude={"expected_version"}), body.expected_version)
    except rv.ReviewError as e:
        raise _bad(e, 409 if "changed elsewhere" in str(e) else 400) from e


@router.post("/drafts")
def create_draft(body: DraftCreate) -> dict:
    if body.origin not in ("manual", "transcript"):
        raise HTTPException(400, "Invalid origin")
    try:
        return rv.create_draft(body.project_id, body.session_id, body.type, body.title, body.description,
                               body.segment_ids, body.note_ids, body.capture_ids, body.origin)
    except rv.ReviewError as e:
        raise _bad(e) from e


@router.post("/drafts/approve")
def approve(body: IdsIn) -> dict:
    try:
        out = {"approved": rv.approve(body.ids)}
    except rv.ReviewError as e:
        raise _bad(e) from e
    _refresh_names(body.ids)
    return out


def _refresh_names(draft_ids: list[str]) -> None:
    from ..services.agents import refresh_name

    with read_session() as s:
        sids = {d.session_id for d in (s.get(DraftItem, i) for i in draft_ids) if d is not None}
    for sid in sids:
        refresh_name(sid)


@router.post("/drafts/merge")
def merge(body: MergeIn) -> dict:
    try:
        return rv.merge(body.ids, body.title)
    except rv.ReviewError as e:
        raise _bad(e) from e


@router.post("/drafts/{did}/{action}")
def draft_action(did: str, action: str) -> dict:
    try:
        if action in ("dismiss", "restore"):
            out = rv.dismiss(did) if action == "dismiss" else rv.restore(did)
            _refresh_names([did])
            return out
    except rv.ReviewError as e:
        raise _bad(e) from e
    raise HTTPException(404, "Unknown action")


@router.post("/drafts/{did}/split")
def split(did: str, body: SplitIn) -> dict:
    try:
        return {"created": rv.split(did, body.parts)}
    except rv.ReviewError as e:
        raise _bad(e) from e


@router.post("/drafts/{did}/attach")
def attach(did: str, body: AttachIn) -> dict:
    try:
        return rv.attach_capture(did, body.capture_id)
    except rv.ReviewError as e:
        raise _bad(e) from e


@router.delete("/drafts/{did}/evidence/{lid}")
def detach(did: str, lid: str) -> dict:
    try:
        return rv.detach_evidence(did, lid)
    except rv.ReviewError as e:
        raise _bad(e) from e


# ================================================================== work items
class ItemPatch(BaseModel):
    type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    work_status: Optional[str] = None
    assignee: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[list[str]] = None
    expected_version: Optional[int] = None


class ItemCreate(BaseModel):
    project_id: str
    type: str = "task"
    title: str
    description: str = ""
    session_id: Optional[str] = None


@router.get("/items")
def items(project_id: Optional[str] = None, q: str = "", type: list[str] = Query(default=[]),  # noqa: A002
          status: list[str] = Query(default=[]), session_id: Optional[str] = None) -> list[dict]:
    return rv.list_work_items(project_id, q, type or None, status or None, session_id)


@router.get("/items/{iid}")
def item(iid: str) -> dict:
    try:
        return rv.get_work_item(iid)
    except rv.ReviewError as e:
        raise _bad(e, 404) from e


@router.patch("/items/{iid}")
def patch_item(iid: str, body: ItemPatch) -> dict:
    patch = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    try:
        out = rv.update_work_item(iid, patch, body.expected_version)
    except rv.ReviewError as e:
        raise _bad(e, 409 if "changed elsewhere" in str(e) else 400) from e
    core().sync.wake()
    return out


@router.post("/items")
def create_item(body: ItemCreate) -> dict:
    try:
        return rv.create_work_item(body.project_id, body.type, body.title, body.description, body.session_id)
    except rv.ReviewError as e:
        raise _bad(e) from e


class ExportIn(BaseModel):
    ids: list[str]
    format: str = "md"
    include_screenshots: bool = True
    include_excerpts: bool = True


@router.post("/export")
def export(body: ExportIn) -> Response:
    from ..services.export import export as do_export

    try:
        data, ctype, name = do_export(body.ids, body.format, body.include_screenshots, body.include_excerpts)
    except ValueError as e:
        raise _bad(e) from e
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ================================================================== devices
@router.get("/audio/devices")
def audio_devices() -> dict:
    from ..audio.devices import list_devices

    return list_devices()


class AudioTestIn(BaseModel):
    kind: str
    device: Optional[str] = None


@router.post("/audio/test")
def audio_test(body: AudioTestIn) -> dict:
    from ..audio.devices import level_test

    if body.kind not in ("mic", "loopback"):
        raise HTTPException(400, "Unknown source")
    a = core().sessions.active
    if a is not None and body.kind in a.recorders:
        raise HTTPException(409, "That source is recording in the current session; its live meter is on the session page.")
    level_test.start(body.kind, body.device)
    return level_test.state


@router.get("/audio/test")
def audio_test_state() -> dict:
    from ..audio.devices import level_test

    return level_test.state


@router.get("/monitors")
def monitors() -> dict:
    from ..capture.screen import foreground_monitor_id, list_monitors

    return {"monitors": list_monitors(), "foreground": foreground_monitor_id()}


# ================================================================== transcription models
@router.get("/transcription/models")
def whisper_models() -> dict:
    from ..transcription.engine import MODEL_CHOICES, downloads, model_installed

    return {"models": [{"name": k, **v, "installed": model_installed(k)} for k, v in MODEL_CHOICES.items()],
            "downloads": downloads.status(), "selected": get_settings().transcription.model}


@router.post("/transcription/models/{name}/download")
def download_model(name: str) -> dict:
    from ..transcription.engine import downloads

    try:
        return downloads.start(name)
    except ValueError as e:
        raise _bad(e) from e


# ================================================================== local AI
@router.get("/ai/status")
def ai_status() -> dict:
    from ..extraction.ollama import OllamaProvider, ProviderError, ensure_running, pulls

    st = get_settings().ai
    ensure_running(st.base_url, wait_s=10)
    prov = OllamaProvider(st.base_url)
    ver = prov.version()
    models: list[dict] = []
    err = None
    if ver:
        try:
            models = prov.list_models()
        except ProviderError as e:
            err = str(e)
    else:
        err = f"Ollama isn't reachable at {st.base_url}."
    installed = any(m["name"] == st.model or m["name"] == f"{st.model}:latest" for m in models)
    return {"reachable": bool(ver), "version": ver, "models": models, "model": st.model, "model_installed": installed,
            "enabled": st.enabled, "error": err, "pulls": pulls.state}


class PullIn(BaseModel):
    model: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:/-]+$")


@router.post("/ai/pull")
def ai_pull(body: PullIn) -> dict:
    from ..extraction.ollama import pulls

    return pulls.start(get_settings().ai.base_url, body.model)


# ================================================================== sharing
class ShareConfigIn(BaseModel):
    server_url: str
    token: Optional[str] = None
    display_name: str = Field(default="", max_length=100)
    allow_insecure_private_network: bool = False


@router.get("/share/status")
def share_status() -> dict:
    from ..sharing.client import load_token

    st = get_settings().sharing
    with read_session() as s:
        jobs = dict(s.execute(select(SyncJob.state, func.count()).group_by(SyncJob.state)).all())
        states = dict(s.execute(select(WorkItem.sharing_state, func.count()).group_by(WorkItem.sharing_state)).all())
    return {"server_url": st.server_url, "display_name": st.display_name, "allow_insecure_private_network": st.allow_insecure_private_network,
            "token_saved": bool(load_token(st.server_url)) if st.server_url else False,
            "outbox": {k: jobs.get(k, 0) for k in ("pending", "failed", "conflict", "done")}, "items": states,
            "offline_reason": core().sync.offline_reason, "last_ok": core().sync.last_ok}


@router.post("/share/config")
def share_config(body: ShareConfigIn) -> dict:
    from ..sharing.client import SharedClient, SharingError, load_token, save_token, validate_server_url

    try:
        url = validate_server_url(body.server_url, body.allow_insecure_private_network)
    except SharingError as e:
        raise _bad(e) from e
    token = (body.token or "").strip() or load_token(url)
    if not token:
        raise HTTPException(400, "Enter the workspace token from your server administrator.")
    update_settings({"sharing": {"allow_insecure_private_network": body.allow_insecure_private_network}})
    try:
        with SharedClient(url, token) as c:
            ws = c.workspace()
    except SharingError as e:
        raise _bad(e) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Couldn't connect to {url}: {e.__class__.__name__}") from e
    save_token(url, token)
    update_settings({"sharing": {"server_url": url, "display_name": body.display_name.strip()}})
    core().sync.wake()
    return {"ok": True, "workspace": ws}


@router.delete("/share/config")
def share_forget() -> dict:
    from ..sharing.client import delete_token

    st = get_settings().sharing
    if st.server_url:
        delete_token(st.server_url)
    update_settings({"sharing": {"server_url": ""}})
    return {"ok": True}


@router.get("/share/projects")
def share_projects() -> list[dict]:
    from ..sharing.client import SharedClient, SharingError

    try:
        with SharedClient() as c:
            return c.projects()
    except SharingError as e:
        raise _bad(e) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Server unreachable: {e.__class__.__name__}") from e


class LinkIn(BaseModel):
    shared_project_id: Optional[str] = None
    name: Optional[str] = None


@router.post("/projects/{pid}/link")
def link_project(pid: str, body: LinkIn) -> dict:
    import uuid as _uuid

    from ..sharing.client import SharedClient, SharingError

    st = get_settings().sharing
    with read_session() as s:
        p = s.get(Project, pid)
        if p is None:
            raise HTTPException(404, "Project not found")
        if p.is_demo:
            raise HTTPException(400, "The demo project can't be shared.")
        pname, pdesc = p.name, p.description
    spid = body.shared_project_id or str(_uuid.uuid4())
    try:
        with SharedClient() as c:
            if body.shared_project_id:
                match = next((x for x in c.projects() if x["id"] == spid), None)
                if match is None:
                    raise HTTPException(404, "Shared project not found on the server")
                name = match["name"]
            else:
                name = (body.name or pname).strip()
                c.put_project(spid, name, pdesc or "")
    except SharingError as e:
        raise _bad(e) from e
    with write_session() as s:
        p = s.get(Project, pid)
        p.shared_project_id, p.shared_project_name, p.shared_server_url = spid, name, st.server_url
        s.flush()
        return project_dict(s, p)


@router.post("/projects/{pid}/unlink")
def unlink_project(pid: str) -> dict:
    with write_session() as s:
        p = s.get(Project, pid)
        if p is None:
            raise HTTPException(404, "Project not found")
        pending = s.scalars(select(SyncJob).join(WorkItem, WorkItem.id == SyncJob.work_item_id)
                            .where(WorkItem.project_id == pid, SyncJob.state.in_(("pending", "failed")))).all()
        for j in pending:
            j.state = "cancelled"
        for w in s.scalars(select(WorkItem).where(WorkItem.project_id == pid, WorkItem.sharing_state.in_(("pending", "failed")))).all():
            w.sharing_state = "local_only" if w.server_version is None else "failed"
            w.sync_error = "Project unlinked from sharing" if w.server_version is not None else None
        p.shared_project_id = p.shared_project_name = p.shared_server_url = None
        s.flush()
        return project_dict(s, p)


class PublishIn(BaseModel):
    ids: list[str]
    include_screenshots: bool = False
    include_excerpts: bool = False
    screenshot_ids: Optional[dict[str, list[str]]] = None


@router.post("/share/preview")
def share_preview(body: PublishIn) -> dict:
    from ..sharing.client import preview

    return preview(body.ids, body.include_screenshots, body.include_excerpts)


@router.post("/share/publish")
def share_publish(body: PublishIn) -> dict:
    from ..sharing.client import SharingError, publish

    try:
        n = publish(body.ids, body.include_screenshots, body.include_excerpts, body.screenshot_ids)
    except SharingError as e:
        raise _bad(e) from e
    core().sync.wake()
    return {"queued": n}


@router.post("/share/retry")
def share_retry() -> dict:
    from ..models import utcnow

    with write_session() as s:
        jobs = s.scalars(select(SyncJob).where(SyncJob.state == "failed")).all()
        for j in jobs:
            j.state, j.attempts, j.next_attempt_at = "pending", 0, utcnow()
            w = s.get(WorkItem, j.work_item_id)
            if w and w.sharing_state == "failed":
                w.sharing_state = "pending"
        n = len(jobs)
    core().sync.wake()
    return {"requeued": n}


@router.post("/projects/{pid}/refresh")
def refresh(pid: str) -> dict:
    from ..sharing.client import SharingError, refresh_project

    try:
        return refresh_project(pid)
    except SharingError as e:
        raise _bad(e) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Couldn't reach the shared server: {e.__class__.__name__}") from e


class ResolveIn(BaseModel):
    keep: str


@router.post("/items/{iid}/resolve")
def resolve(iid: str, body: ResolveIn) -> dict:
    from ..sharing.client import SharingError, resolve_conflict

    try:
        resolve_conflict(iid, body.keep)
    except SharingError as e:
        raise _bad(e) from e
    core().sync.wake()
    return rv.get_work_item(iid)


@router.get("/share/attachments/{aid}")
def share_attachment(aid: str) -> Response:
    from ..sharing.client import SharingError, cached_attachment

    try:
        data, ctype = cached_attachment(aid)
    except (SharingError, ValueError, StorageError) as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Couldn't fetch attachment: {e.__class__.__name__}") from e
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})


# ================================================================== AI assistants (handoffs + connector)
class HandoffIn(BaseModel):
    ids: list[str]
    include_screenshots: bool = True
    include_excerpts: bool = True
    instruction: str = Field(default="", max_length=4000)
    mode: str = "implement_commit"


@router.post("/handoffs")
def create_handoff(body: HandoffIn) -> dict:
    from ..services import handoff as ho

    try:
        return ho.create(body.ids, body.include_screenshots, body.include_excerpts, body.instruction, body.mode)
    except ValueError as e:
        raise _bad(e) from e


@router.get("/handoffs")
def recent_handoffs() -> list[dict]:
    from ..services import handoff as ho

    return ho.list_recent()


@router.post("/ai-copy")
def ai_copy(body: HandoffIn) -> dict:
    """Plain text for pasting into any assistant (screenshots go via the folder)."""
    from ..services import handoff as ho

    try:
        b = ho.build(body.ids, False, body.include_excerpts, instruction=ho.task_text(body.mode, body.instruction, ho.item_types(body.ids), len(set(body.ids))))
    except ValueError as e:
        raise _bad(e) from e
    return {"markdown": b.markdown}


@router.post("/ai-copy/folder")
def ai_copy_folder(body: HandoffIn) -> dict:
    from ..services import handoff as ho

    try:
        folder = ho.write_folder(body.ids, body.include_screenshots, body.include_excerpts)
    except ValueError as e:
        raise _bad(e) from e
    return {"folder": str(folder)}


@router.get("/integrations")
def integrations_status() -> list[dict]:
    from .. import integrations

    return integrations.status()


@router.post("/integrations/{app}/{action}")
def integrations_set(app: str, action: str) -> dict:
    from .. import integrations

    if action not in ("connect", "disconnect"):
        raise HTTPException(404, "Unknown action")
    try:
        return integrations.set_connected(app, action == "connect")
    except integrations.IntegrationError as e:
        raise _bad(e) from e
    except OSError as e:
        raise HTTPException(500, f"Couldn't update the config file: {e}") from e
