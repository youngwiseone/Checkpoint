# Hex ideas for Checkpoint

Reviewed 2 November 2026 using Hex's website, public documentation, and Windows clipboard source. Hex was not installed or tested locally. Its feature documentation distinguishes platforms and calls out behavior covered only by constructed tests; those limits matter when comparing features.

## Most useful ideas

| Priority | Hex behavior | Checkpoint opportunity |
|---|---|---|
| Implemented | Dictation finishes with output ready in another app; Annotate also copies a screen-and-narration bundle. | Automatically copy the latest complete task PNG after saving or processing a voice capture. Refresh it after saved text, screenshot, or evidence edits. Show **Added task to clipboard** using the existing toast. |
| Next | A dedicated **Paste Last Dictation** action recalls the newest output. | Add a configurable **Copy latest task** shortcut. Automatic copying removes the first trip to F9; this shortcut removes another trip when the user subsequently copies something else. Reuse the same PNG handoff and toast. |
| Next | Vocabulary entries accept a preferred spelling and optional “sounds like” spellings. | Expose the existing project glossary in native settings, then add explicit correction aliases for product names, people, and technical terms. Checkpoint already passes its project glossary into dictation; its browser project editor has a glossary field, while the native settings dialog does not. Preserve editable transcripts and avoid applying broad replacements to ordinary words. |
| Next | Dictation recovery handles microphone startup failures with bounded retries and rejects results belonging to abandoned recording attempts. | Extend recovery in short voice notes. Checkpoint's continuous recorder already reconnects, but `NoteDictation` reports a startup or stream failure immediately. Offer a short retry and a way to transcribe retained audio again, with clear feedback if the selected device remains unavailable. Make any switch to another microphone visible. |
| Later | Shortcut modes, status feedback, and model readiness are part of the capture journey. | Add a brief first-use practice capture: microphone selected, speech model downloaded, record a sentence, see the task copied, paste it. Optional start/stop sounds could make the tap-then-hold gesture easier to use while looking at another app. Keep the existing gesture defaults. |

These are recommendations; only automatic task copying is implemented in this change.

## Clipboard behavior to preserve

Hex's Windows paste implementation checks clipboard generations before restoring previous contents. The useful principle for Checkpoint is that asynchronous work must respect a newer clipboard action.

Checkpoint now renders task images on a background thread and writes the clipboard on the Qt thread. A newer edit or task supersedes an older render. A manual copy cancels an automatic copy that is still pending. Later saved edits to the latest task can copy again. Opening F9 and copying one task or multiple selected tasks continues to use the existing image/file formats.

Automatic copying is on by default. **Settings → General → Automatically copy latest task to clipboard** turns it off or on when saved, including for a copy still being processed. Manual copying remains available while automatic copying is off.

The automatic handoff keeps both PNG image data and a local PNG file URL, matching manual single-task copying. It copies the complete task card, including its screenshots and current note. It does not simulate a paste into the foreground app. A rendering failure preserves the clipboard, reports the failure, and leaves the saved task available through F9.

Checkpoint already has local transcription, retained voice evidence, image annotations, task history, and a read-only assistant connection. Hex's wider capture routing and recording machinery would be a separate product decision; the immediate benefit is reducing the steps between capture and paste.

## Sources

- [Hex overview](https://anoma.ly/hex/)
- [Dictation and Paste Last behavior](https://github.com/anomalyco/hex/blob/dev/docs/features/dictation.md)
- [Annotate: screen capture, narration, and clipboard handoff](https://github.com/anomalyco/hex/blob/dev/docs/features/capture.md)
- [Vocabulary and spelling aliases](https://github.com/anomalyco/hex/blob/dev/docs/features/vocabulary.md)
- [Recovery and stale-result handling](https://github.com/anomalyco/hex/blob/dev/docs/features/recovery.md)
- [Windows clipboard snapshots and generation checks](https://github.com/anomalyco/hex/blob/dev/src/windows/paste.rs)
