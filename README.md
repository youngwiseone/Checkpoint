# Checkpoint

Capture a moment. Keep what you said. Work through it in your chat.

Checkpoint is a local Windows capture notebook for testing games, apps and documents.
Screenshots and notes work without recording, models, internet or a server.

## Everyday use

Choose a project once. Its name groups your captures; no repository or agent setup is required.

| Control | Action |
|---|---|
| Tap **F8** | Save a screenshot to the inbox. |
| Double tap **F8** | Add a typed note to the same screenshot. |
| Hold **F8** for about half a second | Review current tasks with screenshots and transcript. |
| **F9** (optional) | Open or close the review panel. |

Remap the keys in Settings → Capture key. Leave the review key empty to use just one key.
A tap captures when you release the key; holding opens review without taking a screenshot.
Double tapping keeps the first screenshot, taken before the note window appears.
In the note window, click the microphone **Dictate note** button, speak, then click **Stop dictation**.
Whistle adds the words to your editable note. **Save** (Ctrl+Enter) saves it with the screenshot;
if still dictating, Save stops and transcribes first. Dictation uses your selected microphone,
works without a recording session, and stops after 60 seconds.

Hold the capture key to open **Inbox**. Edit a title or note, mark something Done, archive it, or reopen it.
Choose **Settings** in that panel for a native settings popup. Everything runs in desktop popups; no browser opens.
Every capture is available immediately. There is no approval or send stage.
Review shows one task at a time, with Previous/Next, Edit, Approve, Delete and Undo buttons.
Review opens centered on the active display as a compact frosted card stack. Swipe or drag
up to approve a task, or down to delete it to Trash. Short drags cancel without changing anything.
Scroll over the card or use the arrow keys to browse without changing its status. Click a card (or press Enter)
to expand its screenshot and edit the task. Settings and nested editors use the same frameless glass style.
The cards behind it preview actual queued tasks. Frosted desktop pixels refresh when the panel
opens and stay in memory; only UI surfaces are blurred and tinted, with clear space between them.
Undo restores the last status change, provided the task has not been edited elsewhere since.
The inbox also offers Undo after a status change. No additional global shortcuts are required.
While editing a task, choose **Edit screenshot**. Crop the area to keep, or highlight a problem
with a red rectangle on the current image. Highlighting preserves an existing crop; choosing a
new crop starts from the original and replaces earlier highlights. Restore original removes all edits.
Review and chat use the edited image. Tasks with several screenshots have a screenshot picker.

## Recording and transcript

In **Settings → Audio**, select microphone and/or computer audio and save. Press **Start recording** in the review panel or tray.
Recording is optional and only runs after you start it. The tray and review panel show its state.
Computer audio records the selected output device. Tell others on a call when you record them.

Download Whistle (~17 MB plus its small CPU runtime) once in Settings → Transcript.
Whistle runs offline and supports English, German, French, Spanish, Italian, Dutch and Polish. Live transcription adds speech
from roughly 20 seconds before to 15 seconds after a capture as it becomes available. A typed note
is never overwritten by speech. The full transcript stays available under **Transcript history** in the review panel;
select lines there to create another task, or double-click a line to correct it.

Use the recording controls or tray to pause, resume or stop recording. Closing a popup keeps
Checkpoint running; Quit in the tray exits it.

## Work in your chat

Connect Codex or Claude Desktop once under Settings → Chat, then restart that app.
Ask your assistant: “Show my latest Checkpoint captures” or “List my open Checkpoint tasks”.
The connector retrieves notes, linked transcript excerpts and screenshots directly from this PC.
It is read-only: mark tasks done in Checkpoint after trying the changes. It does not expose raw audio
or the full transcript.

Checkpoint no longer launches coding agents, creates branches/worktrees, tracks agent progress,
restarts previews, or asks you to approve and send a batch. Automatic screenshots and background
AI organisation are disabled. Existing captures, transcripts and tasks remain in the database;
pending cards and unfinished screenshots join the inbox on startup, while dismissed cards stay dismissed.
Legacy database fields and migrations remain so existing data can still be opened.

## Setup

Requires Windows 10/11 and Python 3.11+. Node.js and a frontend build are not needed for the desktop app.

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
.\start.cmd
```

Everything is stored in `%LOCALAPPDATA%\Checkpoint`. Settings → Storage can move it.
No telemetry or paid AI calls. The desktop app does not start a web server.
Open the native panel from the capture key, tray, or launcher.

## Development

```powershell
cd backend
..\.venv\Scripts\python.exe -m checkpoint.headless --no-browser
```

The optional headless developer server retains the legacy browser interface and prints a one-time login link.
It does not provide native capture keys. This server and the frontend build are separate from the native desktop app.

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests
npm run build --prefix frontend
```

The old orchestration screens, API routes, agent runner, preview manager and their feature-specific
controls/tests have been removed. Capture, transcription, persistence, chat access and optional legacy
sharing/data helpers remain covered by the test suite.

## Limitations

Exclusive fullscreen or protected content can produce black screenshots; use borderless/windowed mode.
Speech recognition can make mistakes. Linked speech is context, not proof of what a screenshot shows.
Windows desktop only; no installer yet.

## License

The evidence-first review, screenshot picker and simple review actions were inspired by
[blurt's focus review](https://github.com/AGIHunt/blurt/blob/main/skills/blurt/scripts/review.html).
They are implemented using Checkpoint's native Qt interface and local task model.

Copyright (C) 2026 Bligh Hedges. GNU Affero General Public License v3 or later; see [LICENSE](LICENSE).
