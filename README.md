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
| **Ctrl+F8** | Start a session for the current project with the same setup as its last session. Press twice within 3 s to end it. |
| **Ctrl+F9** | Review overlay: one card at a time, then send what you approved to the project's coding agent. |
| **Shift+F9** | Switch project: pick one (or type to filter) and a session starts there, ending the running one. Or set up a new project, prefilled from the last one. |

**Set and forget:** pick the project in the sidebar and press **Start session** (or **Ctrl+F8** anywhere). Each
session starts like the project's previous one (audio sources, devices, labels, transcription, auto screenshots).
Under **Offer a session when a program starts…**, pick the program you test (by exe, or by window title for
editors). When it opens, Checkpoint shows a toast and a tray message; press **Ctrl+F8** or click the message to
start. While a session runs, the chips on the Session page and the tray menu turn the microphone, computer audio,
live transcript and auto screenshots on or off immediately.

**Switch projects with Shift+F9.** A card floats beside your work listing your projects, most recently used first,
with the one recording now and the one whose program is in the foreground marked. Type to filter, **↑/↓** (or hover)
to pick, **Enter** (or click) to start a session there: a running session in another project ends first. The last
row, **Ctrl+N** or **Tab** sets up a **new project** with the same settings as the last active one (agent, access,
setup / check / preview commands, what to record), a repo folder next to the last one's when one matches the name,
and the program in the foreground. Change anything, then **Enter** creates it and starts a session. **Alt+←** goes
back to the list, **Esc** hides it and keeps a half-filled new project for next time.

In the note window, **Ctrl+Enter** saves, **Enter** adds a new line, **Esc** keeps the screenshot in
*Unfinished captures* without creating a card, **Discard** removes it. Focus returns to your app.
Shortcuts can be remapped in Settings → Shortcuts; conflicts with other apps are reported.

**Items** has tabs for *To review* (cards), *Open*, *Done* and *All*. Review keys: **J/K** move, **A** keep (approve), **E** edit, **D** dismiss, **U** undo, **X** select.
Review state (Pending/Approved/Dismissed), work status (Open/In progress/Done/Won't do) and sharing
state (Local only/Pending/Synced/Failed/Conflict) are independent — approving never marks work done
and never uploads anything.

Try it with **Load demo project** on the Session page of a new install (clearly labelled synthetic data).

## Review, send, refresh

The everyday loop runs from shortcuts, without opening the main window:

1. **Capture.** F8 / Shift+F8 / F9 as above. With audio on, an F8 press (or an auto screenshot) becomes a
   *suggested* card as soon as the speech around it is transcribed, so you don't have to end the session.
2. **Review with Ctrl+F9.** A stack of cards floats at the side of the screen (the rest of the screen stays visible
   and clickable), each with its screenshot, the next ones waiting behind. **Swipe up** (or **A** / **↑**) to approve:
   the card flies into the *Send N approved* counter. **Swipe down** (or **D** / **↓**) to dismiss, **U** to undo,
   **click** (or **E** / **Enter**) to edit in place (the first line is the title; **Ctrl+Enter** saves),
   **N** rename the session, **R** restart the preview,
   **Tab** task status, **Esc** hides it from anywhere and keeps your place and any unsaved text. Letter keys never act
   while you type. Reviewing the last card goes straight to the send step.
   The overlay opens on the running session, else the project whose program is in the foreground, and only asks
   when several projects have work waiting.
3. **Send with S** (or *Send approved*). It shows how many tasks go where (agent, branch, repo) before sending.
   Only approved, unsent tasks in that session go; dismissed and unreviewed cards stay out, and a task is never
   sent twice. A report that repeats a task that's already in progress is added to it as evidence instead of
   becoming a second fix; if the earlier task was already finished, it's sent as a follow-up to it.
4. **Status.** Approved → Sending → Sent to agent (only once the agent has accepted it) → Working → Ready, or
   **Needs you** with the reason (a question from the agent, failed checks, a preview that didn't start).
5. **Ready to refresh.** After the agent finishes, Checkpoint runs the project's check command and restarts the
   preview from the session's working copy. A task is only Ready when that preview is running a commit that
   contains its change.
6. **Try it, then close the loop** in the overlay's task view (**Tab**, **J/K** to pick): **Y** it works (Done),
   **F** still broken (a follow-up card for that task, ready to type into), **E** answers a task that needs you
   (the answer goes back with it), **A** sends it again.

While you review, the session's working copy is already being made in the background (and its setup command run),
so a send starts straight away. The agent's current step ("Editing water.go") shows in the overlay.

**Names and branches.** A session's name is suggested from its tasks and refined while you review. The first send
locks it and creates `checkpoint/<name>` (with `-2` only if that's taken) in its own worktree under the data folder;
your own checkout is never switched or changed. Every later send from the session lands on the same branch, one
commit per task (`T-3: ...`). When you're happy, **Open pull request** pushes the branch and opens the PR (with `gh`)
or the host's compare page; **Merge** only ever runs when you press it.

**Agents.** Claude Code (`claude`) and Codex (`codex`) run headless in the session's working copy. The prompt asks
them to hand independent tasks to subagents, do overlapping ones in order, commit each task separately, and report
progress through the Checkpoint connector's `report_task` tool (only available to runs Checkpoint started; the
connector stays read-only everywhere else). Later batches in a session resume the same Claude Code conversation.
*Standard* access lets the agent edit files, run `git add/commit` and the check command; *Full* skips permission
prompts inside the working copy. Push, merge, rebase, reset and branch switching are always blocked. Sign the agent
in once (`claude`, then `/login`) before the first send.

**Set up each project once** in Settings → Projects (also offered when you create a project): the repo, base branch,
the program that runs it, the default agent and access, and optional setup / check / preview commands and preview URL.

## Sending items to an AI assistant by hand

In **Items**, tick items (Shift+click selects a range) and press **Send to Claude / Codex**, or press
**Send all N** to send the whole filtered list. With several items the prompt asks the assistant to work
through them in order and report back at the end:

- **Claude Desktop / Codex** — connect them once in **Settings → AI assistants** (then restart that app).
  *Send to AI* copies a short prompt such as “use get_handoff with H-3”; paste it into the chat and the
  assistant fetches the items, verbatim notes and screenshots itself through the read-only Checkpoint
  connector (MCP). You can also just ask it to “list my open Checkpoint bugs”.
- **Any other AI** — *Copy as text* puts the items on the clipboard; *Open screenshots folder* gives you the
  images to drag in. Items → Export also produces a Markdown/JSON/ZIP bundle.

Checkpoint never uploads anything itself, and audio or full transcripts are never included.

## Optional features

- **Transcription** — Settings → Transcription → Download `base.en` (~145 MB, one-time). Runs on CPU (int8);
  NVIDIA GPU optional with automatic CPU fallback. See [docs/models.md](docs/models.md).
- **Local AI organisation** — install [Ollama](https://ollama.com), then Settings → Local AI → download
  `qwen3:4b` and turn it on. Text-only; it never sees screenshots and never calls a cloud service.
- **Auto screenshots** — Settings → Local AI → Auto screenshots. During a session with **live** transcription,
  a Jev-style decision model through Ollama 0.35+ (default `tev1:0.8b`, via `/v1/systemone`) reads each
  transcript line and only returns the chance it points out a problem on screen; it never writes text.
  The last ~90 s of screen is kept in memory. A picked line is narrowed to the sentence that mentions the problem,
  and the frame from ~1.5 s before it was said is saved as a marker, with the sentence as its reason. Filler lines,
  Whisper hallucinations and moments you already captured with F8 are skipped. Shots are at least 45 s apart, and
  the default sensitivity is 0.7 (chatter scores about 0.5).
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

## License

Copyright (C) 2026 Bligh Hedges

Checkpoint is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. It is distributed WITHOUT ANY WARRANTY;
see [LICENSE](LICENSE) for details. If you run a modified version as a network service (for
example the shared server), you must offer its users the corresponding source code.
