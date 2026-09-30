import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mic, MonitorSpeaker, Pause, Play, Square, RotateCw, Camera, Sparkles } from "lucide-react";
import { api } from "../api";
import type { SessionDetail, SourceStatus, TimelineItem } from "../types";
import { Banner, Meter, Modal, Toggle, fmtOffset, useToast, Lightbox } from "../components/ui";
import Checklist from "../components/Checklist";
import RecordingChips, { type ChipState } from "../components/RecordingChips";
import { useAppState, useSettings } from "../state";

const CONTEXT_LABEL: Record<string, string> = {
  typed: "Note added",
  unfinished: "Needs a note",
  speech_nearby: "Speech nearby",
  awaiting_transcription: "Waiting for transcription",
  needs_context: "Needs context",
  transcription_failed: "Transcription failed",
  no_audio: "No audio",
};

function SourceRow({ s, onRetry }: { s: SourceStatus; onRetry: () => void }) {
  const Icon = s.kind === "mic" ? Mic : MonitorSpeaker;
  let status: { text: string; cls: string };
  if (s.state === "paused") status = { text: "Paused — not recording", cls: "warn" };
  else if (s.state === "failed") status = { text: "Stopped — couldn't recover", cls: "bad" };
  else if (s.state === "reconnecting") status = { text: `Interrupted — retrying (attempt ${s.attempt})`, cls: "warn" };
  else if (s.state === "starting") status = { text: "Starting…", cls: "info" };
  else if (s.kind === "loopback" && !s.receiving) status = { text: "Listening — nothing is playing (silence)", cls: "ok" };
  else if (s.kind === "mic" && !s.receiving) status = { text: "No data from device", cls: "warn" };
  else status = { text: s.signal_recent ? "Recording" : "Recording — quiet", cls: "ok" };
  return (
    <div className="source-card" style={{ padding: "10px 0" }}>
      <Icon size={20} className="muted" />
      <div>
        <div className="row"><b>{s.label}</b><span className="muted small truncate">{s.device}</span></div>
        <div className="row" style={{ marginTop: 6 }}>
          <span className={`dot ${status.cls}`} /><span className="small" style={{ minWidth: 240 }}>{status.text}</span>
          <div className="grow"><Meter value={s.level} active={s.receiving && s.state === "recording"} /></div>
        </div>
        {s.error && s.state !== "recording" && (
          <p className="small" style={{ color: s.state === "failed" ? "var(--danger)" : "var(--warn)", marginTop: 4 }}>
            {s.error}{s.interrupted_at_ms != null && ` · interrupted at ${fmtOffset(s.interrupted_at_ms)}`}
          </p>
        )}
        {s.dropped_buffers > 0 && <p className="small" style={{ color: "var(--warn)" }}>{s.dropped_buffers} audio buffer(s) dropped under load.</p>}
        {(s.state === "failed" || s.state === "reconnecting") && <button className="btn sm" style={{ marginTop: 6 }} onClick={onRetry}><RotateCw size={14} /> Retry now</button>}
      </div>
    </div>
  );
}

export function QuickNote() {
  const [text, setText] = useState("");
  const toast = useToast();
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: () => api.post("/api/notes/quick", { text }),
    onSuccess: () => { setText(""); toast("success", "Note saved as a card"); qc.invalidateQueries(); },
    onError: (e: Error) => toast("error", e.message),
  });
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) save.mutate(); }}>
      <label htmlFor="qn" className="label">Quick note</label>
      <textarea id="qn" className="textarea" rows={2} value={text} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); if (text.trim()) save.mutate(); } }}
        placeholder="Something to remember… (Ctrl+Enter to save)" style={{ marginTop: 6 }} />
      <div className="row" style={{ marginTop: 8 }}>
        <span className="hint grow">Same as <kbd>F9</kbd> from anywhere.</span>
        <button className="btn" disabled={!text.trim() || save.isPending}>Save note</button>
      </div>
    </form>
  );
}

export default function ActiveSession() {
  const { data: state } = useAppState();
  const s = state?.session;
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);
  const sid = s?.active ? s.session_id! : null;
  const detail = useQuery({ queryKey: ["session", sid], queryFn: () => api.get<SessionDetail>(`/api/sessions/${sid}`), enabled: !!sid, refetchInterval: 3000 });
  const timeline = useQuery({ queryKey: ["timeline", sid], queryFn: () => api.get<{ items: TimelineItem[] }>(`/api/sessions/${sid}/timeline`), enabled: !!sid, refetchInterval: 3000 });
  const action = useMutation({
    mutationFn: (a: "pause" | "resume" | "end") => api.post<{ session_id?: string }>(`/api/sessions/active/${a}`),
    onSuccess: (r, a) => {
      qc.invalidateQueries();
      if (a === "end" && r.session_id) nav(`/sessions/${r.session_id}`);
    },
    onError: (e: Error) => toast("error", e.message),
  });
  const retry = useMutation({ mutationFn: (kind: string) => api.post(`/api/sessions/active/sources/${kind}/retry`) });
  const { data: settings } = useSettings();
  const live = useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.patch("/api/sessions/active", patch),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["state"] }); qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["session", sid] }); },
    onError: (e: Error) => toast("error", e.message),
  });
  const pauseTranscription = useMutation({
    mutationFn: (paused: boolean) => api.patch("/api/settings", { transcription: { paused } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["state"] }),
  });

  if (!s?.active) return null;

  const items = timeline.data?.items ?? [];
  const captures = items.filter((i) => i.kind === "capture").slice(-8).reverse();
  const recent = items.filter((i) => i.kind === "segment" || i.kind === "note").slice(-6);
  const t = detail.data?.transcription;
  const tw = state!.workers.transcription;
  const sources = s.sources ?? [];
  const backlog = (t?.queued ?? 0) + (t?.running ?? 0);
  const mic = sources.find((x) => x.kind === "mic");
  const loop = sources.find((x) => x.kind === "loopback");
  const chips: ChipState = {
    mic: !!mic, micLabel: mic?.label ?? "", loop: !!loop, loopLabel: loop?.label ?? "", live: s.transcription_mode === "live",
    auto: !!settings?.auto_capture.enabled, ai: !!s.ai_enabled, ask: !!s.always_ask_context,
  };
  const changeLive = (p: Partial<ChipState>) => {
    const body: Record<string, unknown> = {};
    if (p.mic !== undefined) body.mic = { enabled: p.mic };
    if (p.loop !== undefined) body.loopback = { enabled: p.loop };
    if (p.live !== undefined) body.transcription_mode = p.live ? "live" : "after";
    if (p.auto !== undefined) {
      body.auto_capture = p.auto;
      if (p.auto && s.transcription_mode !== "live" && sources.length) body.transcription_mode = "live";
    }
    if (p.ask !== undefined) body.always_ask_context = p.ask;
    live.mutate(body);
  };
  const aw = state!.workers.auto_capture;

  return (
    <div className="page wide">
      <div className="page-head">
        <div className="grow">
          <div className="row">
            <h1 className="truncate">{s.title}</h1>
            {s.paused ? <span className="badge warn">Paused</span> : sources.length ? <span className="badge danger">● Recording</span> : <span className="badge accent">Session running</span>}
          </div>
          <p className="mono" style={{ fontSize: 15 }}>{fmtOffset(s.offset_ms)} elapsed · {detail.data?.project_name}</p>
        </div>
        {s.paused ? (
          <button className="btn" onClick={() => action.mutate("resume")}><Play size={16} /> Resume</button>
        ) : (
          <button className="btn" onClick={() => action.mutate("pause")}><Pause size={16} /> Pause</button>
        )}
        <button className="btn danger" onClick={() => setConfirmEnd(true)}><Square size={15} /> End session</button>
      </div>

      <div className="card stack" style={{ marginBottom: 20 }}>
        <div className="row"><h3 className="grow">Recording</h3><span className="muted small">Changes apply immediately (also from the tray menu)</span></div>
        <RecordingChips v={chips} onChange={changeLive} aiAvailable={!!state?.ai_enabled} disabled={live.isPending} live />
        {chips.auto && aw.state === "needs_live" && <span className="small" style={{ color: "var(--warn)" }}>Auto screenshots are waiting for a live transcript{sources.length ? "" : " — turn on an audio source"}.</span>}
        {chips.auto && aw.state === "watching" && <span className="small text-2"><Sparkles size={13} style={{ verticalAlign: -2 }} /> Watching the transcript · {aw.count} auto screenshot{aw.count === 1 ? "" : "s"} so far{aw.error ? ` · ${aw.error}` : ""}</span>}
      </div>

      <div className="split split-session">
        <div className="stack-lg">
          <div className="card">
            <div className="card-head"><h3>Capture</h3>
              <span className="kbd-hints"><span><kbd>F8</kbd> screenshot</span><span><kbd>Shift+F8</kbd> screenshot + note</span><span><kbd>F9</kbd> text note</span></span>
            </div>
            {!state!.desktop.available && <Banner kind="warn">Global hotkeys need the desktop helper. Launch Checkpoint with <code>start.cmd</code>.</Banner>}
            {state!.desktop.available && state!.desktop.hotkeys.ok === false && <Banner kind="warn">Shortcut problem: {state!.desktop.hotkeys.reason}. <Link to="/settings">Change shortcuts</Link></Banner>}
            {s.paused && <Banner kind="warn">Paused: no audio is being recorded. Screenshots and notes still work{sources.length ? " — F8 will ask for a note while paused." : "."}</Banner>}
            {!sources.length && <Banner kind="info">No audio selected — F8 will ask for a note.</Banner>}
            {sources.length > 0 && !s.paused && !s.audio_functioning && <Banner kind="error">All audio sources have stopped. F8 will ask for a note until recording recovers.</Banner>}
            {sources.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {sources.map((src) => <SourceRow key={src.source_id} s={src} onRetry={() => retry.mutate(src.kind)} />)}
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-head"><h3>Recent captures</h3><span className="muted small">{detail.data?.capture_count ?? 0} total</span></div>
            {!captures.length ? <p className="muted">Nothing captured yet. Press <kbd>F8</kbd> in your game or app.</p> : (
              <div className="row wrap" style={{ gap: 12 }}>
                {captures.map((c) => (
                  <figure key={c.id} style={{ margin: 0 }}>
                    <img src={c.thumb_url} className="thumb" style={{ width: 176, height: 99 }} alt={`Screenshot at ${fmtOffset(c.offset_ms)}`} onClick={() => setZoom(c.image_url!)} />
                    <figcaption className="small muted row" style={{ gap: 6, maxWidth: 176 }}>
                      {c.trigger === "auto" ? <Sparkles size={12} /> : <Camera size={12} />} {fmtOffset(c.offset_ms)} · {CONTEXT_LABEL[c.context_state ?? ""] ?? c.context_state}
                    </figcaption>
                    {c.reason && <figcaption className="small text-2" style={{ maxWidth: 176 }} title={c.reason}>“{c.reason.length > 60 ? c.reason.slice(0, 60) + "…" : c.reason}”</figcaption>}
                  </figure>
                ))}
              </div>
            )}
          </div>

          {sources.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h3>Transcription</h3>
                <Toggle checked={!tw.paused} onChange={(v) => pauseTranscription.mutate(!v)} label={<span className="small">Background transcription</span>} />
              </div>
              <p className="text-2 small">
                {s.transcription_mode === "live" ? "Live: audio is transcribed in the background while you work." : "After session: audio is saved now and transcribed when you end the session."}
                {" "}{backlog > 0 ? `${backlog} audio block${backlog === 1 ? "" : "s"} waiting.` : ""} {t?.done ? `${t.done} done.` : ""}
                {t?.failed ? <span style={{ color: "var(--danger)" }}> {t.failed} failed.</span> : null}
              </p>
              {!tw.model_installed && <p className="small" style={{ color: "var(--warn)", marginTop: 6 }}>Model {tw.model} isn't downloaded yet — audio is kept safely; download it in <Link to="/settings">Settings</Link> to transcribe.</p>}
              {tw.gpu_fallback && <p className="small" style={{ color: "var(--warn)" }}>{tw.gpu_fallback}</p>}
              {recent.length > 0 && (
                <div className="timeline" style={{ marginTop: 10 }}>
                  {recent.map((i) => (
                    <div key={i.id} className="tl-row"><span className="tl-time">{fmtOffset(i.offset_ms)}</span>
                      <span><span className="tl-who">{i.kind === "note" ? "Note" : i.source_label}</span>{i.text}</span><span /></div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="stack-lg">
          <div className="card"><div className="card-head"><h3>Checklist</h3></div><Checklist sessionId={sid!} /></div>
          <div className="card"><QuickNote /></div>
          {!!state?.review.pending && (
            <div className="card row"><span className="grow">{state.review.pending} card{state.review.pending === 1 ? "" : "s"} waiting for review</span><Link to="/items?tab=review" className="btn sm">Review</Link></div>
          )}
        </div>
      </div>

      {confirmEnd && (
        <Modal title="End this session?" onClose={() => setConfirmEnd(false)} footer={
          <>
            <button className="btn" onClick={() => setConfirmEnd(false)}>Keep going</button>
            <button className="btn danger-solid" onClick={() => { setConfirmEnd(false); action.mutate("end"); }}>End session</button>
          </>}>
          <p>Recording stops immediately and everything captured is kept. Transcription{detail.data?.ai_enabled ? " and AI organisation" : ""} will run in the background afterwards.</p>
        </Modal>
      )}
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}
