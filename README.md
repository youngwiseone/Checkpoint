# Checkpoint

Capture issues. Turn them into to-dos. Work through them with your assistant.

Checkpoint is a Windows app for recording bugs, design changes and feature ideas in games, apps or anything else on your screen. Save a screenshot with a typed or dictated note, then send it to Codex or Claude to work through.

It runs quietly in the background until you need it. Capturing and reviewing tasks works locally, without AI tokens, internet or a server.

## Get started

You'll need **Windows 10/11** and **Python 3.11 or newer**.

1. Download this repository and unzip it.
2. Open PowerShell in the extracted folder and run this once to install dependencies:

   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1
   ```

3. Double-click **start.cmd**.
4. Hold **F9** and choose or create a project to group your captures.

Next time, just double-click **start.cmd**. Closing the review window leaves Checkpoint running in the system tray. Use **Quit** in the tray to exit.

## Capture and review

| Control | Action |
|---|---|
| Tap **F9** | Save a screenshot as an open task. |
| Double-tap **F9** | Add or edit its note. |
| Hold **F9** | Review your tasks. |

In the note window, type or click **Dictate note**, then **Save**. For dictation, first download the speech model in **Settings → Transcript**. It runs offline after download.

To capture what you're saying as you test, choose your microphone and/or computer audio in **Settings → Audio**, then start recording from the tray. With transcription enabled, nearby speech is added to your screenshots automatically. Recording is optional.

In review, edit notes or screenshots, drag a task **up to mark Done**, or **down to delete**. Deleted tasks can be reopened.

## Work through your tasks

- **Use images:** Click **Open task images** and drag the generated images into Codex, Claude or your preferred chat. Each includes the screenshot and its notes. Include every page for longer notes.
- **Connect directly:** Connect Codex or Claude Desktop in **Settings → Chat**, restart that app, then ask: “List my open Checkpoint tasks.”

Work through the tasks with your assistant, check the changes, then mark them **Done** in Checkpoint. The direct connection is read-only.

Your data stays in `%LOCALAPPDATA%\Checkpoint`, with task images in its `images` folder. You choose what to share.

## License

Copyright (C) 2026 Bligh Hedges. [GNU Affero General Public License v3 or later](LICENSE).
