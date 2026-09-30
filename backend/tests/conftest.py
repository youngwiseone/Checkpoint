import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
os.environ.setdefault("CHECKPOINT_DATA_DIR", tempfile.mkdtemp(prefix="sc-test-"))


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """A fresh data directory + migrated database for each test (simulates a clean install)."""
    from checkpoint import config, db, settings_store

    config.override_paths(tmp_path / "data")
    db.reset_engine()
    settings_store.invalidate_cache()
    db.migrate()
    yield tmp_path / "data"
    db.reset_engine()
    settings_store.invalidate_cache()


def restart():
    """Simulate an application restart: drop engine + caches, re-run migrations."""
    from checkpoint import db, settings_store

    db.reset_engine()
    settings_store.invalidate_cache()
    db.migrate()


def png_bytes(w=320, h=180, color=(40, 90, 160)):
    import io

    from PIL import Image

    img = Image.new("RGB", (w, h), color)
    b = io.BytesIO()
    img.save(b, "PNG")
    t = io.BytesIO()
    img.save(t, "JPEG")
    return b.getvalue(), t.getvalue()
