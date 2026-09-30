"""Application-owned file storage: atomic writes and path confinement."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from .config import paths


class StorageError(Exception):
    pass


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _confine(base: Path, rel: str) -> Path:
    base = base.resolve()
    candidate = (base / rel).resolve()
    if candidate != base and base not in candidate.parents:
        raise StorageError("Path escapes application storage")
    return candidate


def media_path(rel: str) -> Path:
    return _confine(paths().media, rel)


def audio_path(rel: str) -> Path:
    return _confine(paths().audio, rel)


def shared_cache_path(rel: str) -> Path:
    return _confine(paths().shared_cache, rel)


def dir_size(p: Path) -> int:
    total = 0
    if not p.exists():
        return 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def remove_tree(p: Path, base: Path) -> None:
    p = p.resolve()
    base = base.resolve()
    if base not in p.parents:
        raise StorageError("Refusing to delete outside application storage")
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
