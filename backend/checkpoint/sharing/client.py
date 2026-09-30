"""Shared-server client, token storage and the persistent outbox worker."""

from __future__ import annotations

import ipaddress
import logging
import socket
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from ..db import read_session, write_session
from ..events import notices
from ..models import (
    AudioSource,
    Capture,
    EvidenceLink,
    Note,
    Project,
    Session,
    SyncJob,
    TranscriptSegment,
    WorkItem,
    utcnow,
)
from ..settings_store import get_settings
from ..storage import media_path, shared_cache_path, atomic_write_bytes
from ..workers import Worker

log = logging.getLogger(__name__)
KEYRING_SERVICE = "Checkpoint"
BACKOFF_S = [5, 15, 30, 60, 120, 300]
MAX_ATTEMPTS_BEFORE_FAILED = 4


class SharingError(Exception):
    pass


# ------------------------------------------------------------------ secrets
def _kr_user(url: str) -> str:
    return f"workspace-token:{url.rstrip('/')}"


def save_token(url: str, token: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, _kr_user(url), token)


def load_token(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, _kr_user(url)) or keyring.get_password("SessionCapture", _kr_user(url))
    except Exception:  # noqa: BLE001
        return None


def delete_token(url: str) -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, _kr_user(url))
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ url policy
def _is_private_host(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".lan"):
        return True
    try:
        ips = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    try:
        return all(ipaddress.ip_address(ip.split("%")[0]).is_private or ipaddress.ip_address(ip.split("%")[0]).is_loopback for ip in ips)
    except ValueError:
        return False


def validate_server_url(url: str, allow_insecure_private: bool) -> str:
    url = url.strip().rstrip("/")
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise SharingError("Enter a full server address, e.g. https://capture.example.com")
    if u.scheme == "http":
        if not allow_insecure_private:
            raise SharingError("Use HTTPS. Plain HTTP is only allowed when you enable “Trusted private network”.")
        if not _is_private_host(u.hostname):
            raise SharingError("Plain HTTP is only allowed for private-network or local addresses.")
    return url


class SharedClient:
    def __init__(self, url: Optional[str] = None, token: Optional[str] = None, timeout: float = 20):
        st = get_settings().sharing
        self.url = validate_server_url(url or st.server_url, st.allow_insecure_private_network)
        self.token = token or load_token(self.url)
        if not self.token:
            raise SharingError("No workspace token saved for this server.")
        self.display_name = st.display_name or ""
        self.http = httpx.Client(base_url=self.url, timeout=timeout,
                                 headers={"Authorization": f"Bearer {self.token}", "X-Checkpoint-Display-Name": self.display_name[:100]})

    def __enter__(self) -> "SharedClient":
        return self

    def __exit__(self, *a) -> None:  # noqa: ANN002
        self.http.close()

    def _check(self, r: httpx.Response) -> Any:
        if r.status_code == 401:
            raise SharingError("The server rejected the workspace token. Check it in Settings → Sharing.")
        if r.status_code >= 400 and r.status_code != 409:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise SharingError(f"Server error {r.status_code}: {detail}")
        return r.json() if r.content else None

    def workspace(self) -> dict:
        return self._check(self.http.get("/api/v1/workspace"))

    def projects(self) -> list[dict]:
        return self._check(self.http.get("/api/v1/projects"))

    def put_project(self, pid: str, name: str, description: str = "") -> dict:
        return self._check(self.http.put(f"/api/v1/projects/{pid}", json={"name": name, "description": description}))

    def items(self, pid: str) -> list[dict]:
        return self._check(self.http.get(f"/api/v1/projects/{pid}/items"))

    def put_item(self, iid: str, body: dict) -> tuple[int, dict]:
        r = self.http.put(f"/api/v1/items/{iid}", json=body)
        data = self._check(r)
        return r.status_code, data

    def attachment_exists(self, aid: str, sha: str) -> bool:
        r = self.http.get(f"/api/v1/attachments/{aid}/meta")
        if r.status_code == 404:
            return False
        data = self._check(r)
        return data.get("sha256") == sha

    def upload_attachment(self, aid: str, data: bytes, content_type: str, sha: str) -> None:
        self._check(self.http.put(f"/api/v1/attachments/{aid}", params={"sha256": sha}, content=data,
                                  headers={"Content-Type": content_type}))

    def download_attachment(self, aid: str) -> tuple[bytes, str]:
        r = self.http.get(f"/api/v1/attachments/{aid}")
        if r.status_code >= 400:
            self._check(r)
        return r.content, r.headers.get("content-type", "application/octet-stream")


# ------------------------------------------------------------------ payloads
def item_payload(s, w: WorkItem, attachment_ids: Optional[list[str]] = None, with_excerpts: Optional[bool] = None) -> dict:  # noqa: ANN001
    """Exactly what is sent to the server (also used for the publish preview)."""
    attachments = []
    ids = w.shared_attachment_ids if attachment_ids is None else attachment_ids
    share_excerpts = w.shared_excerpts if with_excerpts is None else with_excerpts
    for cid in ids or []:
        c = s.get(Capture, cid)
        if c is None:
            continue
        p = media_path(c.image_rel_path)
        attachments.append({"id": c.id, "sha256": c.sha256, "content_type": "image/png", "size": p.stat().st_size if p.is_file() else 0})
    excerpts = []
    if share_excerpts:
        labels = {}
        for l in s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == w.id, EvidenceLink.role.in_(("source", "withdrawal")))).all():
            if l.segment_id:
                seg = s.get(TranscriptSegment, l.segment_id)
                if seg:
                    if seg.source_id not in labels:
                        src = s.get(AudioSource, seg.source_id)
                        labels[seg.source_id] = src.label if src else ""
                    excerpts.append({"text": (seg.corrected_text or seg.text)[:2000], "source": labels[seg.source_id][:100], "offset_ms": seg.start_ms})
            elif l.note_id:
                n = s.get(Note, l.note_id)
                if n:
                    excerpts.append({"text": n.text[:2000], "source": "Typed note", "offset_ms": n.offset_ms})
        excerpts = sorted(excerpts, key=lambda e: e["offset_ms"] or 0)[:20]
    sess = s.get(Session, w.session_id) if w.session_id else None
    return {
        "type": w.type, "title": w.title, "description": w.description, "work_status": w.work_status,
        "assignee": w.assignee, "priority": w.priority, "tags": w.tags or [], "attachments": attachments,
        "excerpts": excerpts, "session_title": sess.title if sess else None,
    }


def apply_server_item(w: WorkItem, data: dict) -> None:
    for k in ("type", "title", "description", "work_status", "assignee", "priority"):
        setattr(w, k, data.get(k))
    w.tags = data.get("tags") or []
    w.server_version = data["version"]
    w.updated_by = data.get("updated_by")
    w.shared_attachment_ids = [a["id"] for a in data.get("attachments") or []]
    w.completed_at = utcnow() if w.work_status == "done" and w.completed_at is None else (w.completed_at if w.work_status == "done" else None)


# ------------------------------------------------------------------ publish & refresh
def publish(item_ids: list[str], include_screenshots: bool, include_excerpts: bool,
            screenshot_ids: Optional[dict[str, list[str]]] = None) -> int:
    """Explicit publish: transactional outbox entries. Approval alone never uploads anything."""
    n = 0
    with write_session() as s:
        for iid in dict.fromkeys(item_ids):
            w = s.get(WorkItem, iid)
            if w is None:
                raise SharingError("Item not found")
            p = s.get(Project, w.project_id)
            if not p or not p.shared_project_id:
                raise SharingError(f"“{p.name if p else '?'}” is a local-only project. Link it to a shared project first.")
            if include_screenshots:
                caps = [l.capture_id for l in s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == w.id, EvidenceLink.capture_id.is_not(None))).all()]
                chosen = (screenshot_ids or {}).get(iid)
                w.shared_attachment_ids = [c for c in caps if chosen is None or c in chosen]
            elif w.origin != "shared":
                w.shared_attachment_ids = []
            w.shared_excerpts = include_excerpts
            if w.sharing_state == "conflict":
                continue
            w.sharing_state = "pending"
            w.sync_error = None
            existing = s.scalars(select(SyncJob).where(SyncJob.work_item_id == w.id, SyncJob.state.in_(("pending", "failed")))).first()
            if existing:
                existing.state, existing.attempts, existing.next_attempt_at = "pending", 0, utcnow()
            else:
                s.add(SyncJob(work_item_id=w.id))
            n += 1
    return n


def preview(item_ids: list[str], include_screenshots: bool, include_excerpts: bool) -> dict:
    st = get_settings().sharing
    out = []
    with read_session() as s:
        for iid in item_ids:
            w = s.get(WorkItem, iid)
            if w is None:
                continue
            p = s.get(Project, w.project_id)
            if include_screenshots:
                ids = [l.capture_id for l in s.scalars(select(EvidenceLink).where(EvidenceLink.work_item_id == w.id, EvidenceLink.capture_id.is_not(None))).all()]
            else:
                ids = list(w.shared_attachment_ids or []) if w.origin == "shared" else []
            payload = item_payload(s, w, ids, include_excerpts)
            out.append({"id": w.id, "project": p.name if p else None, "shared_project": p.shared_project_name if p else None,
                        "shareable": bool(p and p.shared_project_id), "payload": payload,
                        "thumbs": [f"/api/media/captures/{a['id']}/thumb" for a in payload["attachments"]]})
    return {"server_url": st.server_url, "display_name": st.display_name, "items": out}


def refresh_project(project_id: str) -> dict:
    with read_session() as s:
        p = s.get(Project, project_id)
        if p is None or not p.shared_project_id:
            raise SharingError("This project isn't linked to a shared project.")
        spid = p.shared_project_id
    with SharedClient() as c:
        remote = c.items(spid)
    added = updated = conflicts = 0
    with write_session() as s:
        for data in remote:
            w = s.get(WorkItem, data["id"])
            if w is None:
                w = WorkItem(id=data["id"], project_id=project_id, origin="shared", title=data["title"], sharing_state="synced",
                             created_by=data.get("created_by"), last_synced_at=utcnow())
                apply_server_item(w, data)
                w.version = 1
                s.add(w)
                added += 1
                continue
            if w.project_id != project_id:
                continue
            if w.server_version is not None and data["version"] <= w.server_version:
                continue
            if w.sharing_state in ("pending", "failed") and w.server_version is not None:
                w.sharing_state = "conflict"
                w.conflict_server_copy = data
                conflicts += 1
                for j in s.scalars(select(SyncJob).where(SyncJob.work_item_id == w.id, SyncJob.state.in_(("pending", "failed")))).all():
                    j.state = "conflict"
            elif w.sharing_state == "conflict":
                w.conflict_server_copy = data
            else:
                apply_server_item(w, data)
                w.version += 1
                w.sharing_state = "synced"
                w.last_synced_at = utcnow()
                updated += 1
        p = s.get(Project, project_id)
        p.last_refreshed_at = utcnow()
    return {"added": added, "updated": updated, "conflicts": conflicts, "total": len(remote)}


def resolve_conflict(item_id: str, keep: str) -> None:
    with write_session() as s:
        w = s.get(WorkItem, item_id)
        if w is None or w.sharing_state != "conflict" or not w.conflict_server_copy:
            raise SharingError("No conflict to resolve")
        server = w.conflict_server_copy
        if keep == "server":
            apply_server_item(w, server)
            w.version += 1
            w.sharing_state = "synced"
            w.last_synced_at = utcnow()
            for j in s.scalars(select(SyncJob).where(SyncJob.work_item_id == w.id, SyncJob.state.in_(("pending", "failed", "conflict")))).all():
                j.state = "cancelled"
        elif keep == "local":
            w.server_version = server["version"]  # our next write is based on what we've now seen
            w.sharing_state = "pending"
            j = s.scalars(select(SyncJob).where(SyncJob.work_item_id == w.id, SyncJob.state.in_(("pending", "failed", "conflict")))).first()
            if j:
                j.state, j.attempts, j.next_attempt_at = "pending", 0, utcnow()
            else:
                s.add(SyncJob(work_item_id=w.id))
        else:
            raise SharingError("Choose keep=local or keep=server")
        w.conflict_server_copy = None
        w.sync_error = None


def cached_attachment(aid: str) -> tuple[bytes, str]:
    """Remote attachment bytes via the authenticated server API, cached locally."""
    import uuid as _uuid

    aid = str(_uuid.UUID(aid))
    with read_session() as s:
        c = s.get(Capture, aid)
        if c is not None and media_path(c.image_rel_path).is_file():
            return media_path(c.image_rel_path).read_bytes(), "image/png"
    p = shared_cache_path(aid)
    meta = shared_cache_path(aid + ".type")
    if p.is_file() and meta.is_file():
        return p.read_bytes(), meta.read_text()
    with SharedClient() as client:
        data, ctype = client.download_attachment(aid)
    if ctype not in ("image/png", "image/jpeg", "image/webp"):
        raise SharingError("Unexpected attachment type from server")
    atomic_write_bytes(p, data)
    atomic_write_bytes(meta, ctype.encode())
    return data, ctype


# ------------------------------------------------------------------ worker
class SyncWorker(Worker):
    name = "sync"
    idle_sleep = 5.0

    def __init__(self) -> None:
        super().__init__()
        self.last_ok: Optional[str] = None
        self.offline_reason: Optional[str] = None

    def step(self) -> bool:
        with read_session() as s:
            job = s.scalars(select(SyncJob).where(SyncJob.state == "pending", SyncJob.next_attempt_at <= utcnow())
                            .order_by(SyncJob.next_attempt_at)).first()
            if job is None:
                return False
            job_id, item_id = job.id, job.work_item_id
        st = get_settings().sharing
        if not st.server_url:
            self._fail(job_id, item_id, "Sharing isn't configured", retry=False)
            return True
        try:
            with SharedClient() as client:
                self._sync_one(client, job_id, item_id)
            self.offline_reason = None
        except (httpx.TransportError, httpx.TimeoutException) as e:
            self.offline_reason = f"Can't reach the shared server ({e.__class__.__name__})."
            self._fail(job_id, item_id, "Saved locally — waiting to sync (server unreachable)", retry=True)
        except SharingError as e:
            self._fail(job_id, item_id, str(e), retry="token" not in str(e).lower())
        return True

    def _fail(self, job_id: str, item_id: str, msg: str, retry: bool) -> None:
        with write_session() as s:
            j = s.get(SyncJob, job_id)
            w = s.get(WorkItem, item_id)
            if j is None:
                return
            j.attempts += 1
            j.last_error = msg
            if retry and j.attempts < MAX_ATTEMPTS_BEFORE_FAILED:
                j.next_attempt_at = utcnow() + timedelta(seconds=BACKOFF_S[min(j.attempts - 1, len(BACKOFF_S) - 1)])
                if w:
                    w.sharing_state = "pending"
                    w.sync_error = msg
            else:
                j.state = "failed"
                if w:
                    w.sharing_state = "failed"
                    w.sync_error = msg

    def _sync_one(self, client: SharedClient, job_id: str, item_id: str) -> None:
        with read_session() as s:
            w = s.get(WorkItem, item_id)
            p = s.get(Project, w.project_id) if w else None
            if w is None:
                return
            if not p or not p.shared_project_id:
                raise SharingError("Project is local-only; nothing was sent.")
            body = item_payload(s, w)
            body.update(project_id=p.shared_project_id, base_version=w.server_version)
            project_meta = (p.shared_project_id, p.shared_project_name or p.name, p.description or "")
            local_version = w.version
            uploads = []
            for a in body["attachments"]:
                c = s.get(Capture, a["id"])
                if c is not None:
                    uploads.append((c.id, media_path(c.image_rel_path), c.sha256))
        client.put_project(*project_meta)
        for aid, path, sha in uploads:
            if client.attachment_exists(aid, sha):
                continue
            if not path.is_file():
                raise SharingError("A selected screenshot is missing locally; deselect it and publish again.")
            client.upload_attachment(aid, path.read_bytes(), "image/png", sha)
        status, data = client.put_item(item_id, body)
        with write_session() as s:
            w = s.get(WorkItem, item_id)
            j = s.get(SyncJob, job_id)
            if status == 409:
                w.sharing_state = "conflict"
                w.conflict_server_copy = data.get("server_item")
                w.sync_error = "Someone else changed this item. Choose which version to keep."
                j.state = "conflict"
                notices.push("warning", f"Sync conflict on “{w.title}”. Choose which version to keep.", "sync")
                return
            if data.get("missing_attachments"):
                raise SharingError("Server is missing attachments; will retry.")
            w.server_version = data["version"]
            w.last_synced_at = utcnow()
            w.sync_error = None
            j.state = "done"
            j.last_error = None
            # If the item was edited locally while we were sending, send again.
            if w.version != local_version:
                w.sharing_state = "pending"
                s.add(SyncJob(work_item_id=w.id))
            else:
                w.sharing_state = "synced"
        self.last_ok = utcnow().isoformat()
