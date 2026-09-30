# Shared server (optional)

A small FastAPI service backed by PostgreSQL that lets a trusted team publish approved items (with
screenshots), see each other's items and update work status. It needs no audio devices, Qt, Whisper or
Ollama, and runs on Windows, Linux or macOS.

**Trust model.** One workspace, one strong API token shared by the team. Anyone with the token can read and
edit every shared item. Display names are attribution labels, not verified accounts. There is no SSO,
invitation flow or per-user roles in this version.

## 1. PostgreSQL

Create a database and a dedicated user (example with `psql`):

```sql
CREATE USER checkpoint WITH PASSWORD 'choose-a-strong-password';
CREATE DATABASE checkpoint OWNER checkpoint;
```

The app never modifies any other database. Review the schema first if you like:
`python -c "from alembic.config import Config; from alembic import command; c=Config(); c.set_main_option('script_location','checkpoint_server/migrations'); c.set_main_option('sqlalchemy.url','postgresql+psycopg://x@y/z'); command.upgrade(c,'head',sql=True)"` (run from `backend/server`).

## 2. Install and configure

```bash
cd backend/server
python -m venv .venv
.venv/bin/pip install -r ../requirements-server.txt      # Windows: .venv\Scripts\pip
python -m checkpoint_server.gentoken                               # prints a strong token (and its SHA-256)
```

Set environment variables (or a service unit / `.env` loaded by your process manager):

| Variable | Meaning |
|---|---|
| `CHECKPOINT_SERVER_DATABASE_URL` | `postgresql+psycopg://checkpoint:PASSWORD@localhost:5432/checkpoint` |
| `CHECKPOINT_SERVER_TOKEN` | the workspace token (≥ 32 chars) — or set `CHECKPOINT_SERVER_TOKEN_SHA256` to store only its hash |
| `CHECKPOINT_SERVER_MEDIA_DIR` | persistent directory for uploaded screenshots, e.g. `/var/lib/checkpoint/media` |
| `CHECKPOINT_SERVER_WORKSPACE_NAME` | shown to clients, e.g. `Cannon Crew team` |
| `CHECKPOINT_SERVER_MAX_UPLOAD_MB` | per-attachment limit (default 20) |

## 3. Run

```bash
python -m checkpoint_server --migrate-only                  # apply migrations
python -m checkpoint_server --host 127.0.0.1 --port 8780    # behind an HTTPS reverse proxy (recommended)
```

Use HTTPS. Either put it behind a reverse proxy that terminates TLS (Caddy, nginx, IIS), or pass
`--ssl-certfile/--ssl-keyfile` to serve TLS directly. Plain HTTP is only accepted by clients that explicitly
enable **Trusted private network**, and only for private-network/local addresses (e.g. a LAN or VPN).

Health check (no auth): `GET /api/v1/health`. Everything else, including screenshot downloads, requires
`Authorization: Bearer <token>`. Uploads are limited to PNG/JPEG/WebP, size-checked, content-sniffed and
SHA-256 verified.

## 4. Connect clients

On each PC: Settings → Sharing → server address, display name, workspace token → **Test & save** (the token
goes into Windows Credential Manager). Then link a project: create a new shared project or join an existing
one. Projects stay local-only unless linked; nothing from local-only projects is ever sent.

Publishing is explicit (Project items → select → **Publish…**), with a preview of the destination and exact
fields. Screenshots and linked excerpts are only included if you tick them; raw audio and full transcripts
never leave the PC. Items are saved locally first, queued in a persistent outbox and retried with backoff;
use **Refresh from server** to pull teammates' changes. Concurrent edits are detected with item versions and
shown as a conflict (keep mine / keep server) — nothing is silently overwritten.

## 5. Backups

Back up both, together:

- the PostgreSQL database (`pg_dump -Fc checkpoint > checkpoint-$(date +%F).dump`), and
- the media directory (`CHECKPOINT_SERVER_MEDIA_DIR`), which holds the screenshot files referenced by the database.

Restore both from the same point in time. Keep the token out of backups of shared folders; rotating it means
setting a new `CHECKPOINT_SERVER_TOKEN` and re-entering it on each client.
