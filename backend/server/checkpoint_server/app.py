"""Checkpoint shared server.

A deliberately small trusted-team server: one workspace, one strong API token,
PostgreSQL for data, a persistent directory for uploaded screenshots. No audio
devices, Qt, Whisper or Ollama are needed here.

Configuration (environment variables):
  CHECKPOINT_SERVER_DATABASE_URL   postgresql+psycopg://user:pass@host:5432/checkpoint
  CHECKPOINT_SERVER_TOKEN          workspace API token (>= 32 chars) — or CHECKPOINT_SERVER_TOKEN_SHA256
  CHECKPOINT_SERVER_MEDIA_DIR      persistent directory for attachments
  CHECKPOINT_SERVER_WORKSPACE_NAME display name of the workspace
  CHECKPOINT_SERVER_MAX_UPLOAD_MB  default 20
"""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ItemType = Literal["bug", "improvement", "idea", "task", "question", "note"]
WorkStatus = Literal["open", "in_progress", "done", "wont_do"]
ALLOWED_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SharedProject(Base):
    __tablename__ = "shared_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SharedItem(Base):
    __tablename__ = "shared_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    work_status: Mapped[str] = mapped_column(String(20), default="open")
    assignee: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tags: Mapped[Any] = mapped_column(JSON, default=list)
    attachments: Mapped[Any] = mapped_column(JSON, default=list)  # [{id, sha256, content_type, size}]
    excerpts: Mapped[Any] = mapped_column(JSON, default=list)  # [{text, source, offset_ms}]
    session_title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(40))
    size: Mapped[int] = mapped_column(Integer)
    rel_path: Mapped[str] = mapped_column(String(200))
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ request bodies
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class AttachmentRef(BaseModel):
    id: str
    sha256: str = Field(min_length=64, max_length=64)
    content_type: str
    size: int = Field(ge=0)

class Excerpt(BaseModel):
    text: str = Field(max_length=2000)
    source: str = Field(default="", max_length=100)
    offset_ms: Optional[int] = None

class ItemIn(BaseModel):
    project_id: str
    base_version: Optional[int] = None
    force: bool = False
    type: ItemType
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20000)
    work_status: WorkStatus = "open"
    assignee: Optional[str] = Field(default=None, max_length=100)
    priority: Optional[str] = Field(default=None, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=20)
    excerpts: list[Excerpt] = Field(default_factory=list, max_length=20)
    session_title: Optional[str] = Field(default=None, max_length=300)


# ------------------------------------------------------------------ config
class Config:
    def __init__(self) -> None:
        self.database_url = os.environ.get("CHECKPOINT_SERVER_DATABASE_URL", "")
        token = os.environ.get("CHECKPOINT_SERVER_TOKEN", "")
        self.token_sha256 = os.environ.get("CHECKPOINT_SERVER_TOKEN_SHA256", "").lower() or (hashlib.sha256(token.encode()).hexdigest() if token else "")
        if token and len(token) < 32:
            raise RuntimeError("CHECKPOINT_SERVER_TOKEN must be at least 32 characters. Generate one with: python -m checkpoint_server.gentoken")
        self.media_dir = Path(os.environ.get("CHECKPOINT_SERVER_MEDIA_DIR", "./server-media")).resolve()
        self.workspace_name = os.environ.get("CHECKPOINT_SERVER_WORKSPACE_NAME", "Shared workspace")
        self.max_upload = int(os.environ.get("CHECKPOINT_SERVER_MAX_UPLOAD_MB", "20")) * 1024 * 1024
        if not self.database_url:
            raise RuntimeError("CHECKPOINT_SERVER_DATABASE_URL is not set")
        if not self.token_sha256:
            raise RuntimeError("CHECKPOINT_SERVER_TOKEN (or CHECKPOINT_SERVER_TOKEN_SHA256) is not set")


def create_app(config: Optional[Config] = None) -> FastAPI:
    cfg = config or Config()
    cfg.media_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(cfg.database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI(title="Checkpoint shared server", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine

    @contextmanager
    def db() -> Iterator:
        s = SessionLocal()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def auth(authorization: str = Header(default="")) -> None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Missing workspace token")
        digest = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(digest, cfg.token_sha256):
            raise HTTPException(401, "Invalid workspace token")

    def actor(x_checkpoint_display_name: str = Header(default="")) -> Optional[str]:
        # Display names are attribution only — not verified identities.
        return x_checkpoint_display_name.strip()[:100] or None

    def _uuid(v: str) -> str:
        try:
            return str(uuid.UUID(v))
        except ValueError as e:
            raise HTTPException(400, "Invalid id") from e

    def item_out(i: SharedItem, s) -> dict:  # noqa: ANN001
        missing = []
        for a in i.attachments or []:
            if s.get(Attachment, a["id"]) is None:
                missing.append(a["id"])
        return {
            "id": i.id, "project_id": i.project_id, "type": i.type, "title": i.title, "description": i.description,
            "work_status": i.work_status, "assignee": i.assignee, "priority": i.priority, "tags": i.tags or [],
            "attachments": i.attachments or [], "excerpts": i.excerpts or [], "session_title": i.session_title,
            "version": i.version, "created_by": i.created_by, "updated_by": i.updated_by,
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "updated_at": i.updated_at.isoformat() if i.updated_at else None, "missing_attachments": missing,
        }

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"ok": True, "service": "checkpoint-server"}

    @app.get("/api/v1/workspace", dependencies=[Depends(auth)])
    def workspace() -> dict:
        return {"name": cfg.workspace_name}

    @app.get("/api/v1/projects", dependencies=[Depends(auth)])
    def projects() -> list[dict]:
        with db() as s:
            rows = s.scalars(select(SharedProject).order_by(SharedProject.name)).all()
            return [{"id": p.id, "name": p.name, "description": p.description, "created_by": p.created_by} for p in rows]

    @app.put("/api/v1/projects/{project_id}", dependencies=[Depends(auth)])
    def put_project(project_id: str, body: ProjectIn, who: Optional[str] = Depends(actor)) -> dict:
        pid = _uuid(project_id)
        with db() as s:
            p = s.get(SharedProject, pid)
            if p is None:
                p = SharedProject(id=pid, name=body.name, description=body.description, created_by=who)
                s.add(p)
            return {"id": p.id, "name": p.name, "description": p.description, "created_by": p.created_by}

    @app.get("/api/v1/projects/{project_id}/items", dependencies=[Depends(auth)])
    def list_items(project_id: str) -> list[dict]:
        pid = _uuid(project_id)
        with db() as s:
            if s.get(SharedProject, pid) is None:
                raise HTTPException(404, "Shared project not found")
            rows = s.scalars(select(SharedItem).where(SharedItem.project_id == pid).order_by(SharedItem.updated_at.desc())).all()
            return [item_out(i, s) for i in rows]

    CONTENT_FIELDS = ("type", "title", "description", "work_status", "assignee", "priority", "tags", "attachments", "excerpts", "session_title")

    @app.put("/api/v1/items/{item_id}", dependencies=[Depends(auth)])
    def put_item(item_id: str, body: ItemIn, response: Response, who: Optional[str] = Depends(actor)) -> dict:
        iid = _uuid(item_id)
        pid = _uuid(body.project_id)
        for a in body.attachments:
            _uuid(a.id)
            if a.content_type not in ALLOWED_TYPES:
                raise HTTPException(415, "Unsupported attachment type")
        data = body.model_dump()
        data["attachments"] = [a.model_dump() for a in body.attachments]
        data["excerpts"] = [e.model_dump() for e in body.excerpts]
        with db() as s:
            if s.get(SharedProject, pid) is None:
                raise HTTPException(404, "Shared project not found")
            item = s.get(SharedItem, iid, with_for_update=True)
            if item is None:
                item = SharedItem(id=iid, project_id=pid, version=1, created_by=who, updated_by=who,
                                  **{k: data[k] for k in CONTENT_FIELDS})
                s.add(item)
                s.flush()
                response.status_code = 201
                return item_out(item, s)
            if item.project_id != pid:
                raise HTTPException(409, "Item belongs to another project")
            same = all(getattr(item, k) == data[k] for k in CONTENT_FIELDS)
            if same:
                return item_out(item, s)  # idempotent retry
            if not body.force and body.base_version != item.version:
                return JSONResponse(status_code=409, content={"detail": "conflict", "server_item": item_out(item, s)})
            for k in CONTENT_FIELDS:
                setattr(item, k, data[k])
            item.version += 1
            item.updated_by = who
            item.updated_at = utcnow()
            s.flush()
            return item_out(item, s)

    @app.get("/api/v1/items/{item_id}", dependencies=[Depends(auth)])
    def get_item(item_id: str) -> dict:
        with db() as s:
            item = s.get(SharedItem, _uuid(item_id))
            if item is None:
                raise HTTPException(404, "Item not found")
            return item_out(item, s)

    @app.get("/api/v1/attachments/{att_id}/meta", dependencies=[Depends(auth)])
    def attachment_meta(att_id: str) -> dict:
        with db() as s:
            a = s.get(Attachment, _uuid(att_id))
            if a is None:
                raise HTTPException(404, "Attachment not uploaded")
            return {"id": a.id, "sha256": a.sha256, "content_type": a.content_type, "size": a.size}

    @app.put("/api/v1/attachments/{att_id}", dependencies=[Depends(auth)])
    async def put_attachment(att_id: str, request: Request, sha256: str, who: Optional[str] = Depends(actor)) -> dict:
        aid = _uuid(att_id)
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype not in ALLOWED_TYPES:
            raise HTTPException(415, "Only PNG, JPEG or WebP screenshots are accepted")
        declared = int(request.headers.get("content-length") or 0)
        if declared > cfg.max_upload:
            raise HTTPException(413, "Attachment too large")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > cfg.max_upload:
                raise HTTPException(413, "Attachment too large")
        data = bytes(body)
        if not data.startswith(MAGIC[ctype]):
            raise HTTPException(415, "File content doesn't match its type")
        digest = hashlib.sha256(data).hexdigest()
        if digest != sha256.lower():
            raise HTTPException(400, "Checksum mismatch — upload was corrupted")
        with db() as s:
            existing = s.get(Attachment, aid)
            if existing is not None:
                if existing.sha256 != digest:
                    raise HTTPException(409, "A different file already exists with this id")
                return {"id": aid, "sha256": digest, "stored": True}
            rel = f"{aid[:2]}/{aid}{ALLOWED_TYPES[ctype]}"
            target = cfg.media_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".up-")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
            s.add(Attachment(id=aid, sha256=digest, content_type=ctype, size=len(data), rel_path=rel, uploaded_by=who))
        return {"id": aid, "sha256": digest, "stored": True}

    @app.get("/api/v1/attachments/{att_id}", dependencies=[Depends(auth)])
    def get_attachment(att_id: str) -> FileResponse:
        with db() as s:
            a = s.get(Attachment, _uuid(att_id))
            if a is None:
                raise HTTPException(404, "Attachment not found")
            p = (cfg.media_dir / a.rel_path).resolve()
            if cfg.media_dir not in p.parents or not p.is_file():
                raise HTTPException(404, "Attachment file missing on server")
            return FileResponse(p, media_type=a.content_type, headers={"Cache-Control": "private, max-age=3600"})

    return app


def run_migrations(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config as AConfig

    cfg = AConfig()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
