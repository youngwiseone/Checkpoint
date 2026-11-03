# Optional models

Neither model is required. Without them, screenshots, typed notes, checklists, review and sharing all work,
and recorded audio is kept safely until you decide to transcribe it.

## Transcription (Whistle)

1. Open **Settings → Transcript**, select `whistle` (approximately 17 MB) and click **Download selected model**.
   Progress and errors are shown there. Checkpoint downloads the model and its native runtime only when requested.
   Files go to `%LOCALAPPDATA%\Checkpoint\models\whistle\whistle` on Windows or
   `~/.local/share/Checkpoint/models/whistle/whistle` on Linux (or your configured data folder).
2. After downloading, transcription runs locally on the CPU without an internet connection.

- Whistle supports seven languages and supplies word timestamps for saved transcripts.
- Choose **Live**, **After recording** or **Off** timing in Settings → Transcript.
- Background transcription can be paused at any time; recording continues and the backlog resumes later.
- The project glossary is passed to the recogniser to help with names like “Sir Spin A Lot”.

## Local AI organisation (Ollama)

1. Install Ollama from https://ollama.com (or your Linux distribution's package). Checkpoint starts its local service
   (`ollama serve` on `http://127.0.0.1:11434`) in the background whenever AI features need it.
2. Settings → Local AI → **Download qwen3:4b with Ollama** (≈2.5 GB), or run `ollama pull qwen3:4b`.
3. Turn **Local AI organisation** on. New sessions can then enable “Organise with local AI after the session”,
   and any ended session has **Organise now / Reprocess**.

How it behaves:

- Text only: transcripts, typed notes, planned checks and screenshot *timestamps*. It never sees image pixels.
- Structured output is constrained with a JSON schema, validated, and every card must cite existing source
  lines; unsupported suggestions are discarded and quotes are rebuilt from your stored text.
- Long sessions are processed in bounded, overlapping parts and then reconciled (repeats merged,
  later corrections and withdrawals applied, conflicts flagged).
- Reprocessing keeps your edits, approvals and dismissals and doesn't duplicate cards.
- Thinking mode is disabled for this task; timeouts and invalid output are retried a bounded number of
  times and then shown as a clear failure. There is no cloud fallback.
- Any model you have in Ollama can be selected; accuracy varies by model and by session.
