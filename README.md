# Checkpoint

**Capture the moment. Keep the context. Turn it into action.**

A local-first Windows companion for playtests, software reviews, report reviews and solo work.
Start a session → optionally record your microphone and/or computer audio → press **F8** for a
screenshot or **F9** for a quick note → review tidy cards afterwards → track them locally or
publish them to a small shared server.

Screenshots and notes always work, with no microphone, no AI models, no internet and no server.
Transcription (faster-whisper) and organisation (Ollama) are optional helpers.

## Quick start (Windows 10/11)

Prerequisites: **Python 3.11+** and **Node.js 20+** (Node is only needed to build the UI). No admin rights, WSL, Docker or accounts.

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1
```

```bash
start.cmd
```

`setup.ps1` creates `.venv`, installs pinned packages, builds the interface and runs a preflight
check. It never downloads models. `start.cmd` checks prerequisites, starts the app without a
console window, and opens the UI in your browser. A tray icon shows the real state
(idle / recording / paused / audio problem) and has **Open**, **Pause/Resume**, **End session** and **Quit**.
Closing the browser tab doesn't stop anything. Use `start.cmd --console` to run attached to a terminal
for troubleshooting.

## Using it

| Shortcut | What happens |
|---|---|
| **F8** | Screenshot of the display with the active app. If audio is recording: saved + timeline marker + a small toast that doesn't steal focus. If no audio is recording: a note window opens next to your work. |
| **Shift+F8** | Screenshot and always ask for a note. |
| **F9** | Text-only quick note. |

In the note window, **Ctrl+Enter** saves, **Enter** adds a new line, **Esc** keeps the screenshot in
*Unfinished captures* without creating a card, **Discard** removes it. Focus returns to your app.
Shortcuts can be remapped in Settings → Shortcuts; conflicts with other apps are reported.

Review inbox keys: **J/K** move, **A** keep (approve), **E** edit, **D** dismiss, **U** undo, **X** select.
Review state (Pending/Approved/Dismissed), work status (Open/In progress/Done/Won't do) and sharing
state (Local only/Pending/Synced/Failed/Conflict) are independent — approving never marks work done
and never uploads anything.

Try it with **Projects & sessions → Load demo project** (clearly labelled synthetic data).

## Sending items to an AI assistant

Select items in **Project items** (or open one) and press **Send to AI**:

- **Claude Desktop / Codex** — connect them once in **Settings → AI assistants** (then restart that app).
  *Send to AI* copies a short prompt such as “use get_handoff with H-3”; paste it into the chat and the
  assistant fetches the items, verbatim notes and screenshots itself through the read-only Checkpoint
  connector (MCP). You can also just ask it to “list my open Checkpoint bugs”.
- **Any other AI** — *Copy as text* puts the items on the clipboard; *Open screenshots folder* gives you the
  images to drag in. Project items → Export also produces a Markdown/JSON/ZIP bundle.

Checkpoint never uploads anything itself, and audio or full transcripts are never included.

## Optional features

- **Transcription** — Settings → Transcription → Download `base.en` (~145 MB, one-time). Runs on CPU (int8);
  NVIDIA GPU optional with automatic CPU fallback. See [docs/models.md](docs/models.md).
- **Local AI organisation** — install [Ollama](https://ollama.com), then Settings → Local AI → download
  `qwen3:4b` and turn it on. Text-only; it never sees screenshots and never calls a cloud service.
- **Shared workspace** — run the small server in [`backend/server`](backend/server) with PostgreSQL; see
  [docs/shared-server.md](docs/shared-server.md).

## Data, privacy and security

- Everything lives in `%LOCALAPPDATA%\Checkpoint` (database, screenshots, audio chunks, models, logs).
  Change it with `CHECKPOINT_DATA_DIR` or Settings → Storage. Nothing personal is written into the repository.
- Recording only happens in a session you started, with the sources you switched on, shown before every
  session and in the tray. Tell others on a call that you're recording. Computer-audio capture records
  the whole selected output device, not a single app.
- The local API binds to `127.0.0.1` only, validates the Host header, and requires a per-launch secret held
  in an HttpOnly SameSite=Strict cookie (handed to the browser through a single-use link the app opens)
  plus a custom header and Origin check on every change — other websites can't start recording or read
  screenshots.
- The shared-workspace token is stored in Windows Credential Manager, never in the browser or `.env`.
- No telemetry, analytics or paid AI calls.

## Development

```bash
cd backend && ..\.venv\Scripts\python.exe -m checkpoint.headless --no-browser
```

Runs the API without tray/hotkeys (captures that need a note go to the Unfinished inbox). For UI work, set
`CHECKPOINT_DEV=1`, run the headless backend on the default port 8765, open the printed one-time link, then run
`npm run dev` in `frontend/` and use http://127.0.0.1:5173. VS Code launch configs are in `.vscode/launch.json`.

Checks:

```bash
cd backend && ..\.venv\Scripts\python.exe -m pytest -q tests
```

```bash
cd frontend && npm run build
```

### Layout

```
backend/checkpoint/   desktop app: core, api/, capture/, audio/, transcription/, extraction/, sharing/, services/, desktop/ (Qt host)
backend/checkpoint/migrations/   Alembic migrations (SQLite)
backend/server/checkpoint_server/  optional shared server (FastAPI + PostgreSQL) with its own migrations
frontend/                 React + TypeScript + Vite UI (served by the backend after build)
```

## Known limitations

- Exclusive-fullscreen or DRM-protected content may capture as a black image; use borderless/windowed mode.
  The app warns when a screenshot looks blank.
- Source labels are not speaker identification. Computer audio may contain several people, game audio or music.
- Near-live transcription works on ~20 s audio blocks with a 1.5 s overlap; a word split exactly at a block
  boundary can occasionally be lost or clipped.
- The local model can misread conversations; every suggestion is a draft linked to its exact source lines.
- Windows only for the desktop app; no installer or `.exe` yet.
