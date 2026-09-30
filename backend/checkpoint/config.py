"""Application paths and process-level configuration.

Everything user-specific (database, screenshots, recordings, models) lives in an
application-data directory outside the repository. The location can be moved with
the CHECKPOINT_DATA_DIR environment variable or a `location.json` pointer file in the
default directory (written by Settings → Storage).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Checkpoint"


def _load_dotenv() -> None:
    """Minimal .env loader (repo root), without overriding real environment variables."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break


_load_dotenv()


def _legacy_root() -> Path:
    """Data folder used before the app was renamed from "Session Capture" to Checkpoint."""
    return _default_root().parent / "SessionCapture"


def _migrate_legacy_root() -> Path:
    """One-time move of the old data folder to the new name. Falls back to the old folder if
    it can't be moved (for example while the old version is still running)."""
    new, old = _default_root(), _legacy_root()
    if new.exists() or not old.is_dir():
        return new
    try:
        os.rename(old, new)
        return new
    except OSError:
        return old


def _migrate_legacy_db(root: Path) -> None:
    old = root / "sessioncapture.db"
    if old.is_file() and not (root / "checkpoint.db").exists():
        try:
            for suffix in ("", "-wal", "-shm"):
                src = root / f"sessioncapture.db{suffix}"
                if src.exists():
                    os.rename(src, root / f"checkpoint.db{suffix}")
        except OSError:
            pass


def resolve_data_dir() -> Path:
    env = os.environ.get("CHECKPOINT_DATA_DIR")
    if env:
        return Path(env).expanduser()
    root = _migrate_legacy_root()
    pointer = root / "location.json"
    if pointer.is_file():
        try:
            target = json.loads(pointer.read_text(encoding="utf-8")).get("data_dir")
            if target:
                return Path(target)
        except (OSError, ValueError):
            pass
    return root


def set_data_dir_pointer(new_dir: Path) -> None:
    root = _default_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "location.json").write_text(json.dumps({"data_dir": str(new_dir)}), encoding="utf-8")


@dataclass
class Paths:
    root: Path

    @property
    def db_file(self) -> Path:
        _migrate_legacy_db(self.root)
        return self.root / "checkpoint.db"

    @property
    def media(self) -> Path:
        return self.root / "media"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def shared_cache(self) -> Path:
        return self.root / "shared-cache"

    def ensure(self) -> None:
        for p in (self.root, self.media, self.audio, self.models, self.logs, self.exports, self.shared_cache):
            p.mkdir(parents=True, exist_ok=True)


IS_WINDOWS = sys.platform == "win32"
HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("CHECKPOINT_PORT", "8765"))
DEV_MODE = os.environ.get("CHECKPOINT_DEV") == "1"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

_paths: Paths | None = None


def paths() -> Paths:
    global _paths
    if _paths is None:
        _paths = Paths(resolve_data_dir())
        _paths.ensure()
    return _paths


def override_paths(root: Path) -> Paths:
    """Used by tests and scripts to point the app at a throwaway directory."""
    global _paths
    _paths = Paths(root)
    _paths.ensure()
    return _paths
