# Checkpoint

Capture a moment. Keep what you said. Work through it in your chat.

Checkpoint is a local Windows capture notebook for testing games, apps and documents.
Screenshots and notes work without recording, models, internet or a server.

## How it works now

1. Start Checkpoint and choose or create a project in the review panel.
2. Tap the capture key while testing. Checkpoint saves the screenshot immediately and creates
   an Open task. Double tap to explain the problem in a typed or dictated note.
3. Hold the capture key to review your tasks, edit their notes or screenshots, and browse
   the evidence. Optional recording adds nearby speech as transcription finishes.
4. Click **Open task images** and drag the generated PNGs into your chat, or connect your
   chat app under **Settings → Chat** and ask it to retrieve the tasks directly.
5. Try the resulting changes, then mark the task Done in Checkpoint. Move unwanted tasks
   to Deleted; you can reopen them later.

The inbox is the task list: captures do not need a separate approval or send step.
Checkpoint keeps the screenshot, your note and nearby speech together; the assistant works
on the changes in your chat. PNG cards are generated automatically from that saved context.

## Everyday use

Choose a project once. Its name groups your captures; no repository or agent setup is required.

| Control | Action |
|---|---|
| Tap **F8** | Save a screenshot to the inbox. |
| Double tap **F8** | Add a typed note to the same screenshot. |
| Hold **F8** for about half a second | Review current tasks with screenshots and transcript. |
| **F9** (optional) | Open or close the review panel. |

Remap the keys in **Settings → General → Capture key**. Leave the review key empty to use just one key.
Choose **Screenshot area** there to capture the active display or active window.
A tap captures when you release the key; holding opens review without taking a screenshot.
Double tapping keeps the first screenshot, taken before the note window appears.
In the note window, click the microphone **Dictate note** button, speak, then click **Stop dictation**.
Whistle adds the words to your editable note. **Save** (Ctrl+Enter) saves it with the screenshot;
if still dictating, Save stops and transcribes first. Dictation uses your selected microphone,
works without a recording session, and stops after 60 seconds.
**Keep for later** or Esc closes the note window while keeping the screenshot in the inbox.

Hold the capture key to open the floating review panel. Choose a project in the header,
or select **New project…**, type a name and press Enter. Captures use the selected project;
during a recording session they belong to that session's project, and the project picker is locked
until recording ends. Saved audio choices apply to the next recording. The cog opens Settings
and × or Esc closes review.
Search, status and session filters share one row above the cards, including captures made without recording.
Review fills the active screen’s available desktop area with a small floating margin.
Cards run from oldest interaction on the left to newest on the right: capturing, editing and changing status count;
background transcription and PNG regeneration do not reorder them. Review starts at the newest card
or resumes your saved position for that project, status and session. The focused title wraps in full,
with its chronological number above it and the total count below the cards. Faded outer previews
and Older/Newer cues indicate additional cards beyond the immediate neighbours.

Screenshots fill tall rounded cards, cropped only in the preview. The full image remains available
in the editor and exported PNG. Notes appear over the screenshot and expand on hover.
Drag left/right or scroll to browse, click a neighbouring card to focus it, and click the current
card or **Click to edit** beneath it to edit. Swipe up to mark Done, down to move to Deleted.
The left/right arrow keys also browse; Delete moves the focused task to Deleted.
Short or diagonal drags settle back; horizontal browsing never changes task status.
Empty projects and empty filter results leave the card area clear. A temporary spinner card shows
queued/running transcription for the selected project or session and disappears when processing finishes.
It reports speech context being added to captures; it is not an exported task.

A brief Undo action restores the last status change; Ctrl+Z also works, provided the task
has not been edited elsewhere since. Right-click offers Edit, Reopen and status actions.
Desktop pixels behind the panel are blurred in memory when review opens; this backdrop is
not saved as a capture. Cards float above it with shadows and headers outside their image surfaces.
While editing a task, choose **Edit screenshot**. Crop the area to keep, or highlight a problem
with a red rectangle on the current image. Highlighting preserves an existing crop; choosing a
new crop starts from the original and replaces earlier highlights. Restore original removes all edits.
Review and chat use the edited image. Tasks with several screenshots have a screenshot picker.

## Recording and transcript

In **Settings → Audio**, select microphone and/or computer audio and save. Start recording from the tray.
Recording is optional and only runs after you start it. The tray and review panel show its state.
Computer audio records the selected output device. Tell others on a call when you record them.

Download Whistle (~17 MB plus its small CPU runtime) once in Settings → Transcript.
Whistle runs offline and supports English, German, French, Spanish, Italian, Dutch and Polish. Live transcription adds speech
from roughly 20 seconds before to 15 seconds after a capture as it becomes available. A typed note
is never overwritten by speech. **Settings → Transcript** offers **Live**, **After recording**
and **Off** modes. After recording defers transcription until the session ends; Off leaves
audio untranscribed. The tray provides recording controls; session and status filters in review
replace the separate History button.

Use the recording controls or tray to pause, resume or stop recording. Closing a popup keeps
Checkpoint running; Quit in the tray exits it.

## Work in your chat

Captures and text notes also become portable PNG cards automatically, usually within a few seconds.
Choose **Open task images** to open the selected project's unfinished images and drag them
into Codex. The output lives in `%LOCALAPPDATA%\Checkpoint\images` (or `images` inside your
chosen Storage location), with a separate project folder containing **Open**, **Done** and **Deleted**.
Done and Deleted views offer buttons for their corresponding folders. Recording is optional.
The folder button opens the project's status folder, including tasks outside the current
search or session filter. **Done / archived** includes completed and archived tasks;
**All** shows every status, while its folder button opens unfinished task images.
Each PNG shows the full screenshot with the current note and linked speech below it;
text-only notes produce an image too. Long notes continue across numbered images—include every page.
Edits overwrite the existing PNGs. Status changes move all pages between folders, with Deleted
kept for recovery. Corrected speech and screenshot edits also refresh the PNGs. Existing flat
`exports/cards` outputs migrate automatically; manually saved copies elsewhere remain separate.

The original PNG also stores exact Unicode context in its `Checkpoint.Context` iTXt field,
and structured item data in `Checkpoint.Item`. An assistant with access to the original file can
extract the text with Pillow:

```python
from PIL import Image
with Image.open("checkpoint-card.png") as image:
    print(image.info["Checkpoint.Context"])
```

Chat uploads can resize images or remove metadata, so the visible text panel is the primary
way to carry context. Embedded text does not guarantee a chat assistant can access it.

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

Everything is stored in `%LOCALAPPDATA%\Checkpoint`. **Settings → Storage** chooses a data
folder for the next launch; copy existing data there before restarting, because files are not
moved automatically. Developers can also set `CHECKPOINT_DATA_DIR`.
No telemetry or paid AI calls. The desktop app does not start a web server.
Open the native panel from the capture key, tray, or launcher.

The data folder contains `checkpoint.db` for tasks and settings, `media` for screenshots,
`audio` for recordings, `models` for local transcription, `images` for portable task PNGs,
and `logs` for diagnostics. Running `start.cmd` again opens the existing instance.
Use `start.cmd --console` to run with a console when troubleshooting.

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
