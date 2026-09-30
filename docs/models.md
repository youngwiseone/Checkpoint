# Optional models

Neither model is required. Without them, screenshots, typed notes, checklists, review and sharing all work,
and recorded audio is kept safely until you decide to transcribe it.

## Transcription (faster-whisper)

1. Settings → Transcription → **Download** next to `base.en` (≈145 MB, recommended starting point).
   Progress and errors are shown there. Files go to `%LOCALAPPDATA%\Checkpoint\models\whisper\<name>`.
2. After downloading, transcription runs fully offline.

| Model | Size | Notes |
|---|---|---|
| tiny.en | ~75 MB | fastest, least accurate |
| base.en | ~145 MB | default; fine on CPU |
| small.en | ~485 MB | better accuracy, slower on CPU |
| medium.en / distil-large-v3 | ~1.5 GB | GPU recommended |

- **CPU** uses int8 and a small number of threads (Settings → Transcription → CPU threads) so it interferes
  little with games. **After session** timing is recommended: nothing heavy runs while you work.
- **GPU (CUDA)**: choose *NVIDIA GPU* in Settings. It needs CUDA 12 and cuDNN 9 runtime libraries available to
  CTranslate2 (see the faster-whisper README). If loading fails the app shows a message and uses the CPU.
- Background transcription can be paused at any time; recording continues and the backlog resumes later.
- Transcription and AI organisation run one at a time so they don't compete for memory.
- The project glossary is passed to the recogniser to help with names like “Sir Spin A Lot”.

## Local AI organisation (Ollama)

1. Install Ollama for Windows from https://ollama.com. Checkpoint starts its local service
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
