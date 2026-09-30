import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Camera, Check, FileAudio, Pencil, Play, Plus, RotateCw, Sparkles, Trash2, X, Wand2, StopCircle, Inbox } from "lucide-react";
import { api, qs } from "../api";
import type { Capture, ItemType, SessionDetail as SD, TimelineItem } from "../types";
import { Banner, Empty, Modal, Spinner, TYPES, TYPE_LABEL, fmtBytes, fmtDate, fmtDuration, fmtOffset, useToast, Lightbox } from "../components/ui";
import Checklist from "../components/Checklist";
import { useAppState } from "../state";

const CATS: { v: string; l: string }[] = [{ v: "", l: "No category" }, ...TYPES.map((t) => ({ v: t, l: TYPE_LABEL[t] }))];

export function CaptureNoteModal({ capture, onClose }: { capture: Capture | TimelineItem; onClose: () => void }) {
  const [text, setText] = useState("");
  const [cat, setCat] = useState("");
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: () => api.post(`/api/captures/${capture.id}/note`, { text, category: cat || null }),
    onSuccess: () => { qc.invalidateQueries(); toast("success", "Saved as a card for review"); onClose(); },
    onError: (e: Error) => toast("error", e.message),
  });
  const discard = useMutation({
    mutationFn: () => api.post(`/api/captures/${capture.id}/discard`),
    onSuccess: () => { qc.invalidateQueries(); onClose(); },
    onError: (e: Error) => toast("error", e.message),
  });
  return (
    <Modal title="Add context to screenshot" onClose={onClose} footer={
      <>
        <button className="btn danger" onClick={() => discard.mutate()} disabled={discard.isPending}><Trash2 size={15} /> Discard screenshot</button>
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Keep for later</button>
        <button className="btn primary" disabled={!text.trim() || save.isPending} onClick={() => save.mutate()}>Save card</button>
      </>}>
      <div className="stack">
        <img src={("image_url" in capture && capture.image_url) || ""} alt="Screenshot" className="thumb lg" />
        <div className="field">
          <label htmlFor="cn">What should we remember about this?</label>
          <textarea id="cn" className="textarea" value={text} onChange={(e) => setText(e.target.value)} autoFocus
            onKeyDown={(e) => { if (e.key === "Enter" && e.ctrlKey && text.trim()) { e.preventDefault(); save.mutate(); } }} />
        </div>
        <div className="field" style={{ maxWidth: 260 }}>
          <label htmlFor="cc">Category <span className="muted">(optional)</span></label>
          <select id="cc" className="select" value={cat} onChange={(e) => setCat(e.target.value)}>{CATS.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}</select>
        </div>
      </div>
    </Modal>
  );
}

function CreateCardModal({ sessionId, projectId, segs, captureId, onClose }: { sessionId: string; projectId: string; segs: TimelineItem[]; captureId?: string; onClose: () => void }) {
  const joined = segs.map((s) => s.text).join(" ");
  const [type, setType] = useState<ItemType>("note");
  const [title, setTitle] = useState(joined.length > 110 ? joined.slice(0, 107) + "…" : joined);
  const [desc, setDesc] = useState(joined);
  const qc = useQueryClient();
  const toast = useToast();
  const create = useMutation({
    mutationFn: () => api.post("/api/drafts", {
      project_id: projectId, session_id: sessionId, type, title, description: desc, origin: "transcript",
      segment_ids: segs.filter((s) => s.kind === "segment").map((s) => s.id), note_ids: segs.filter((s) => s.kind === "note").map((s) => s.id),
      capture_ids: captureId ? [captureId] : [],
    }),
    onSuccess: () => { qc.invalidateQueries(); toast("success", "Card created — it's in the review inbox"); onClose(); },
    onError: (e: Error) => toast("error", e.message),
  });
  return (
    <Modal title="Create a card" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}>Create card</button></>}>
      <div className="stack">
        <div className="field"><label htmlFor="ct">Type</label>
          <select id="ct" className="select" value={type} onChange={(e) => setType(e.target.value as ItemType)} style={{ maxWidth: 220 }}>{TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}</select></div>
        <div className="field"><label htmlFor="ctt">Title</label><input id="ctt" className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        <div className="field"><label htmlFor="ctd">Description</label><textarea id="ctd" className="textarea" rows={4} value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
        <p className="hint">{segs.length} source line{segs.length === 1 ? "" : "s"}{captureId ? " and the screenshot" : ""} will be linked as evidence.</p>
      </div>
    </Modal>
  );
}

function SegmentRow({ item, selected, onToggle, sessionEnded }: { item: TimelineItem; selected: boolean; onToggle: () => void; sessionEnded: boolean }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.text ?? "");
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: (t: string | null) => api.patch(`/api/segments/${item.id}`, { corrected_text: t }),
    onSuccess: () => { setEditing(false); qc.invalidateQueries({ queryKey: ["timeline"] }); },
    onError: (e: Error) => toast("error", e.message),
  });
  const play = () => {
    if (!item.audio_url) return;
    if (playing && audio.current) { audio.current.pause(); setPlaying(false); return; }
    const a = new Audio(item.audio_url);
    audio.current = a;
    a.onended = () => setPlaying(false);
    a.onerror = () => { setPlaying(false); toast("error", "Audio for this line isn't available (it may have been deleted)."); };
    void a.play();
    setPlaying(true);
  };
  return (
    <div className={`tl-row ${selected ? "selected" : ""}`}>
      <label className="tl-time check" style={{ gap: 6 }}>
        <input type="checkbox" checked={selected} onChange={onToggle} aria-label={`Select line at ${fmtOffset(item.offset_ms)}`} disabled={!sessionEnded && false} />
        {fmtOffset(item.offset_ms)}
      </label>
      <div>
        {editing ? (
          <form className="row" onSubmit={(e) => { e.preventDefault(); save.mutate(text); }}>
            <input className="input" value={text} onChange={(e) => setText(e.target.value)} autoFocus aria-label="Corrected text" onKeyDown={(e) => e.key === "Escape" && setEditing(false)} />
            <button className="btn sm primary" type="submit">Save</button>
            {item.corrected && <button className="btn sm" type="button" onClick={() => save.mutate(null)}>Revert</button>}
          </form>
        ) : (
          <span>
            <span className="tl-who">{item.kind === "note" ? (item.note_kind === "quick" ? "Quick note" : "Typed note") : item.source_label}</span>
            <span className={item.low_confidence ? "low-conf" : ""} title={item.low_confidence ? "Low recognition confidence — play it back to check" : undefined}>{item.text}</span>
            {item.corrected && <span className="muted small" title={`Recognised as: ${item.raw_text}`}> (corrected)</span>}
          </span>
        )}
      </div>
      <div className="row" style={{ gap: 2 }}>
        {item.kind === "segment" && item.audio_url && <button className="icon-btn" onClick={play} aria-label={playing ? "Stop playback" : "Play this audio"}>{playing ? <StopCircle size={16} /> : <Play size={16} />}</button>}
        {item.kind === "segment" && !editing && <button className="icon-btn" onClick={() => { setText(item.text ?? ""); setEditing(true); }} aria-label="Correct text"><Pencil size={15} /></button>}
      </div>
    </div>
  );
}

function OrganisePanel({ d, aiEnabled }: { d: SD; aiEnabled: boolean }) {
  const { data: appState } = useAppState();
  const tw = appState?.workers.transcription;
  const qc = useQueryClient();
  const toast = useToast();
  const run = d.latest_run;
  const organise = useMutation({
    mutationFn: () => api.post(`/api/sessions/${d.id}/organise`, { force: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["session", d.id] }),
    onError: (e: Error) => toast("error", e.message),
  });
  const cancel = useMutation({ mutationFn: (id: string) => api.post(`/api/runs/${id}/cancel`), onSuccess: () => qc.invalidateQueries({ queryKey: ["session", d.id] }) });
  const transcribe = useMutation({
    mutationFn: () => api.post(`/api/sessions/${d.id}/transcribe`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["session", d.id] }); toast("info", "Transcription queued"); },
    onError: (e: Error) => toast("error", e.message),
  });
  const t = d.transcription;
  const total = t.queued + t.running + t.done + t.failed;
  const active = run && (run.state === "queued" || run.state === "running");
  const stats = run?.stats as Record<string, number | string | boolean> | undefined;
  return (
    <div className="card stack">
      <div className="card-head" style={{ marginBottom: 0 }}><h3>Processing</h3></div>
      {d.sources.length === 0 ? (
        <p className="text-2 small">No audio was recorded in this session. Typed notes are already cards.</p>
      ) : (
        <div>
          <div className="row"><FileAudio size={16} className="muted" /><b className="small">Transcription</b>
            <span className="small text-2">{total === 0 ? "No audio blocks" : `${t.done} of ${total} audio blocks done`}{t.running ? " · working…" : ""}{t.queued && d.transcription_mode === "off" ? " · not started" : ""}</span>
          </div>
          {total > 0 && <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${(t.done / Math.max(1, total)) * 100}%` }} /></div>}
          {tw?.stalled && tw.error && t.running > 0 && <p className="small" style={{ color: "var(--danger)", marginTop: 6 }}>{tw.error}</p>}
          {tw?.gpu_fallback && total > t.done && <p className="small" style={{ color: "var(--warn)", marginTop: 6 }}>{tw.gpu_fallback}</p>}
          {!tw?.model_installed && t.queued > 0 && <p className="small" style={{ color: "var(--warn)", marginTop: 6 }}>Model {tw?.model} isn't downloaded yet. Your audio is kept safely; download it in <Link to="/settings">Settings</Link> to transcribe.</p>}
          {t.failed > 0 && <p className="small" style={{ color: "var(--danger)", marginTop: 6 }}>{t.failed} block(s) failed: {d.transcription_errors.join("; ")}</p>}
          {(t.failed > 0 || (d.transcription_mode === "off" && t.queued > 0)) && (
            <button className="btn sm" style={{ marginTop: 8 }} onClick={() => transcribe.mutate()}><RotateCw size={14} /> {t.failed ? "Retry transcription" : "Transcribe now"}</button>
          )}
          {d.raw_audio_deleted && <p className="small muted" style={{ marginTop: 6 }}>Raw audio was deleted; playback is unavailable.</p>}
        </div>
      )}
      <hr className="divider" style={{ margin: "4px 0" }} />
      <div>
        <div className="row"><Sparkles size={16} className="muted" /><b className="small">Local AI organisation</b>
          {run && <span className={`badge ${run.state === "done" ? "success" : run.state === "failed" ? "danger" : run.state === "cancelled" ? "neutral" : "accent"}`}>{run.state === "done" ? "Done" : run.state === "failed" ? "Failed" : run.state === "cancelled" ? "Cancelled" : run.state === "queued" ? "Queued" : "Running"}</span>}
        </div>
        {!aiEnabled ? (
          <p className="small text-2" style={{ marginTop: 4 }}>Off. Typed notes are already cards; select transcript lines below to make cards yourself, or enable Local AI in <Link to="/settings">Settings</Link>.</p>
        ) : (
          <>
            {active && (
              <>
                <p className="small text-2" style={{ marginTop: 4 }}>{run!.stage}</p>
                <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${Math.round(run!.progress * 100)}%` }} /></div>
                <button className="btn sm" style={{ marginTop: 8 }} onClick={() => cancel.mutate(run!.id)}><X size={14} /> Cancel</button>
              </>
            )}
            {run?.state === "failed" && <p className="small" style={{ color: "var(--danger)", marginTop: 4 }}>{run.error}</p>}
            {run?.state === "done" && stats && (
              <p className="small text-2" style={{ marginTop: 4 }}>
                {String(stats.created ?? 0)} new · {String(stats.updated ?? 0)} updated · {String(stats.preserved_human ?? 0)} kept as you edited them
                {stats.withdrawn ? ` · ${stats.withdrawn} withdrawn in conversation` : ""}{stats.rejected_invalid_ids ? ` · ${stats.rejected_invalid_ids} unsupported suggestion(s) discarded` : ""}
                {stats.untranscribed_chunks ? ` · ${stats.untranscribed_chunks} audio block(s) weren't transcribed yet` : ""} · {run.model}
              </p>
            )}
            {!active && d.state !== "active" && d.state !== "paused" && (
              <button className="btn sm" style={{ marginTop: 8 }} onClick={() => organise.mutate()} disabled={organise.isPending}>
                <Wand2 size={14} /> {run ? "Reprocess" : "Organise now"}
              </button>
            )}
            {!active && run && <p className="hint" style={{ marginTop: 6 }}>Reprocessing keeps your edits, approvals and dismissals.</p>}
          </>
        )}
      </div>
    </div>
  );
}

function Recap({ sessionId }: { sessionId: string }) {
  type R = { planned: unknown[]; completed: unknown[]; outstanding: { id: string; text: string; state: string; work_item_id: string | null }[]; suggested_done: unknown[]; new_items: { pending: number; approved: number; dismissed: number; possibly_completed: { id: string; title: string }[]; by_type: Record<string, number> } };
  const q = useQuery({ queryKey: ["recap", sessionId], queryFn: () => api.get<R>(`/api/sessions/${sessionId}/recap`) });
  const qc = useQueryClient();
  const toast = useToast();
  const keep = useMutation({
    mutationFn: () => api.post<{ created: number }>(`/api/sessions/${sessionId}/keep-outstanding`),
    onSuccess: (r) => { qc.invalidateQueries(); toast("success", `${r.created} check(s) added to project items as tasks`); },
    onError: (e: Error) => toast("error", e.message),
  });
  if (!q.data) return <Spinner />;
  const r = q.data;
  const unkept = r.outstanding.filter((o) => o.state !== "outstanding" && !o.work_item_id);
  return (
    <div className="stack-lg">
      <div className="grid-3">
        <div className="card stat"><span className="v">{r.completed.length}<span className="muted" style={{ fontSize: 16 }}> / {r.completed.length + r.outstanding.length}</span></span><span className="k">Checks completed</span></div>
        <div className="card stat"><span className="v">{r.outstanding.length}</span><span className="k">Still outstanding</span></div>
        <div className="card stat"><span className="v">{r.new_items.pending + r.new_items.approved}</span><span className="k">New ideas & issues ({r.new_items.pending} to review)</span></div>
      </div>
      <div className="grid-2">
        <div className="card">
          <div className="card-head"><h3>Checklist</h3>
            {unkept.length > 0 && <button className="btn sm" onClick={() => keep.mutate()} disabled={keep.isPending}>Keep {unkept.length} outstanding as tasks</button>}
          </div>
          <Checklist sessionId={sessionId} />
        </div>
        <div className="card stack">
          <h3>Captured in this session</h3>
          <div className="row wrap">{Object.entries(r.new_items.by_type).filter(([, n]) => n).map(([t, n]) => <span key={t} className={`badge type-${t}`}>{n} {TYPE_LABEL[t as ItemType]}</span>)}</div>
          {r.new_items.possibly_completed.length > 0 && (
            <div>
              <div className="section-label" style={{ marginTop: 6 }}>Possibly completed — confirm in review</div>
              {r.new_items.possibly_completed.map((d) => <div key={d.id} className="small">• {d.title}</div>)}
            </div>
          )}
          <p className="text-2 small">Approved {r.new_items.approved} · dismissed {r.new_items.dismissed}. Nothing is marked done automatically.</p>
          <div><Link to={`/review${qs({ session: sessionId })}`} className="btn sm"><Inbox size={14} /> Review this session's cards</Link></div>
        </div>
      </div>
    </div>
  );
}

function UnlinkedScreenshots({ d }: { d: SD }) {
  const q = useQuery({ queryKey: ["unlinked", d.id], queryFn: () => api.get<Capture[]>(`/api/captures/unlinked${qs({ session_id: d.id })}`), refetchInterval: 5000 });
  const [make, setMake] = useState<Capture | null>(null);
  const [note, setNote] = useState<Capture | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  if (!q.data?.length) return null;
  const label: Record<string, string> = { speech_nearby: "Speech nearby", awaiting_transcription: "Waiting for transcription", needs_context: "Needs context — no speech found nearby", transcription_failed: "Transcription failed nearby" };
  return (
    <div className="card">
      <div className="card-head"><h3>Screenshots without a card</h3><span className="muted small">Markers from F8 or auto screenshots while audio was recording</span></div>
      <div className="stack">
        {q.data.map((c) => (
          <div key={c.id} className="row top" style={{ gap: 14 }}>
            <img src={c.thumb_url} className="thumb" style={{ width: 176, height: 99 }} alt={`Screenshot at ${fmtOffset(c.offset_ms)}`} onClick={() => setZoom(c.image_url)} />
            <div className="grow">
              <div className="row"><b className="mono">{fmtOffset(c.offset_ms)}</b>
                {c.trigger === "auto" && <span className="badge neutral" title="Picked by the auto-screenshot model from the live transcript">Auto</span>}
                <span className={`badge ${c.context_state === "needs_context" ? "warn" : c.context_state === "speech_nearby" ? "accent" : "neutral"}`}>{label[c.context_state ?? ""] ?? c.context_state}</span></div>
              {c.nearby?.slice(0, 3).map((n) => <p key={n.id} className="small text-2">[{fmtOffset(n.offset_ms)}] {n.text}</p>)}
              <div className="row" style={{ marginTop: 6 }}>
                {!!c.nearby?.length && <button className="btn sm" onClick={() => setMake(c)}><Plus size={14} /> Card from nearby speech</button>}
                <button className="btn sm" onClick={() => setNote(c)}><Pencil size={14} /> Type a note</button>
              </div>
            </div>
          </div>
        ))}
      </div>
      {make && <CreateCardModal sessionId={d.id} projectId={d.project_id} captureId={make.id} onClose={() => setMake(null)}
        segs={(make.nearby ?? []).map((n) => ({ kind: "segment", id: n.id, offset_ms: n.offset_ms, text: n.text }))} />}
      {note && <CaptureNoteModal capture={note} onClose={() => setNote(null)} />}
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}

function DangerZone({ d }: { d: SD }) {
  const [confirm, setConfirm] = useState<"audio" | "session" | null>(null);
  const impact = useQuery({ queryKey: ["impact", d.id], queryFn: () => api.get<{ audio_bytes: number; screenshot_bytes: number; audio_chunks: number; captures: number; approved_items: number; items_using_screenshots: number; pending_cards: number }>(`/api/sessions/${d.id}/impact`), enabled: !!confirm });
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const delAudio = useMutation({ mutationFn: () => api.post(`/api/sessions/${d.id}/delete-audio`), onSuccess: () => { setConfirm(null); qc.invalidateQueries(); toast("success", "Raw audio deleted. Transcript and cards are kept."); }, onError: (e: Error) => toast("error", e.message) });
  const delSession = useMutation({ mutationFn: () => api.del(`/api/sessions/${d.id}${qs({ confirm: "true" })}`), onSuccess: () => { qc.invalidateQueries(); nav("/"); }, onError: (e: Error) => toast("error", e.message) });
  const i = impact.data;
  return (
    <div className="card">
      <div className="card-head"><h3>Storage & retention</h3></div>
      <div className="row wrap">
        {d.sources.length > 0 && !d.raw_audio_deleted && <button className="btn sm" onClick={() => setConfirm("audio")}><FileAudio size={14} /> Delete raw audio…</button>}
        <button className="btn sm danger" onClick={() => setConfirm("session")} disabled={d.state === "active" || d.state === "paused"}><Trash2 size={14} /> Delete session…</button>
      </div>
      {confirm && (
        <Modal title={confirm === "audio" ? "Delete raw audio?" : "Delete this session?"} onClose={() => setConfirm(null)} footer={
          <><button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
            <button className="btn danger-solid" disabled={!i} onClick={() => (confirm === "audio" ? delAudio.mutate() : delSession.mutate())}>Delete</button></>}>
          {!i ? <Spinner /> : confirm === "audio" ? (
            <div className="stack"><p>Frees {fmtBytes(i.audio_bytes)} ({i.audio_chunks} audio blocks). Transcripts, cards and approved items stay.</p>
              <Banner kind="warn">You won't be able to replay audio to check misheard words, or re-transcribe with a better model.</Banner></div>
          ) : (
            <div className="stack">
              <p>Deletes the session's {i.captures} screenshot(s), notes, transcript, {fmtBytes(i.audio_bytes)} of audio and {i.pending_cards} pending card(s).</p>
              <p><b>{i.approved_items} approved item(s) are kept</b> in Items.</p>
              {i.items_using_screenshots > 0 && <Banner kind="warn">{i.items_using_screenshots} approved item(s) use screenshots or quotes from this session — they will lose that evidence.</Banner>}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}

export default function SessionDetail() {
  const { id } = useParams();
  const { data: state } = useAppState();
  const [tab, setTab] = useState<"recap" | "timeline">("recap");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [noteFor, setNoteFor] = useState<TimelineItem | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  const d = useQuery({ queryKey: ["session", id], queryFn: () => api.get<SD>(`/api/sessions/${id}`), refetchInterval: (q) => (q.state.data && (q.state.data.transcription.queued + q.state.data.transcription.running > 0 || ["queued", "running"].includes(q.state.data.latest_run?.state ?? "")) ? 2000 : 8000) });
  const tl = useQuery({ queryKey: ["timeline", id], queryFn: () => api.get<{ items: TimelineItem[] }>(`/api/sessions/${id}/timeline`), enabled: tab === "timeline", refetchInterval: 6000 });
  const items = tl.data?.items ?? [];
  const selectedItems = useMemo(() => items.filter((i) => sel.has(i.id)), [items, sel]);
  if (d.isLoading) return <div className="page"><Spinner label="Loading session…" /></div>;
  if (d.error || !d.data) return <div className="page"><Banner kind="error">{(d.error as Error)?.message ?? "Session not found"}</Banner></div>;
  const s = d.data;
  if (s.state === "active" || s.state === "paused") {
    return <div className="page"><Banner kind="info" action={<Link to="/" className="btn sm">Open</Link>}>This session is running.</Banner></div>;
  }
  const toggle = (iid: string) => setSel((x) => { const n = new Set(x); if (n.has(iid)) n.delete(iid); else n.add(iid); return n; });
  return (
    <div className="page wide">
      <div className="page-head">
        <div className="grow">
          <div className="row"><h1 className="truncate">{s.title}</h1>{s.is_demo && <span className="badge accent">Demo</span>}{s.state === "interrupted" && <span className="badge warn">Interrupted</span>}</div>
          <p>{s.project_name} · {fmtDate(s.started_at)} · {fmtDuration(s.started_at, s.ended_at ?? s.last_heartbeat_at)} · {s.capture_count} screenshot{s.capture_count === 1 ? "" : "s"}
            {s.sources.map((x) => ` · ${x.label}`).join("")}</p>
          {s.purpose && <p className="text-2">{s.purpose}</p>}
        </div>
        <Link to={`/review${qs({ session: s.id })}`} className="btn primary"><Inbox size={16} /> Review cards{s.pending_cards ? ` (${s.pending_cards})` : ""}</Link>
      </div>
      {s.audio_events.filter((e) => e.kind === "lost_on_crash" || e.kind === "interrupted").length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <Banner kind="warn" icon={<AlertTriangle size={18} />}>
            {s.audio_events.filter((e) => e.kind === "lost_on_crash" || e.kind === "interrupted").map((e, i) => (
              <p key={i} className="small">At {fmtOffset(e.offset_ms)}: {e.message || e.kind}. Audio during the interruption was not recorded.</p>
            ))}
          </Banner>
        </div>
      )}
      <div className="split split-right">
        <div>
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "recap"} className={`tab ${tab === "recap" ? "active" : ""}`} onClick={() => setTab("recap")}>Recap</button>
            <button role="tab" aria-selected={tab === "timeline"} className={`tab ${tab === "timeline" ? "active" : ""}`} onClick={() => setTab("timeline")}>Timeline & transcript</button>
          </div>
          {tab === "recap" ? (
            <div className="stack-lg"><Recap sessionId={s.id} /><UnlinkedScreenshots d={s} /></div>
          ) : (
            <div className="card">
              <div className="row" style={{ marginBottom: 10 }}>
                <span className="grow text-2 small">Select lines to turn them into a card. Dotted underline = low recognition confidence.</span>
                {sel.size > 0 && <>
                  <button className="btn sm ghost" onClick={() => setSel(new Set())}>Clear</button>
                  <button className="btn sm primary" onClick={() => setCreating(true)}><Plus size={14} /> Create card from {sel.size} line{sel.size === 1 ? "" : "s"}</button>
                </>}
              </div>
              {tl.isLoading ? <Spinner /> : !items.length ? <Empty title="Nothing on the timeline yet">No transcript, notes or screenshots.</Empty> : (
                <div className="timeline">
                  {items.map((i) => {
                    if (i.kind === "segment" || i.kind === "note") return <SegmentRow key={i.id} item={i} selected={sel.has(i.id)} onToggle={() => toggle(i.id)} sessionEnded />;
                    if (i.kind === "capture")
                      return (
                        <div key={i.id} className="tl-row marker">
                          <span className="tl-time">{fmtOffset(i.offset_ms)}</span>
                          <div className="row top">
                            <img src={i.thumb_url} className="thumb" alt={`Screenshot at ${fmtOffset(i.offset_ms)}`} onClick={() => setZoom(i.image_url!)} />
                            <div className="small">
                              <div className="row"><Camera size={14} /> Screenshot{i.window_title ? ` · ${i.window_title}` : ""}</div>
                              <div className="muted">{i.context_state === "typed" ? "Has a typed note" : i.context_state === "needs_context" ? "Needs context — no speech nearby" : i.context_state === "unfinished" ? "Unfinished — no note yet" : i.context_state === "awaiting_transcription" ? "Waiting for transcription" : i.context_state === "speech_nearby" ? "Speech nearby" : i.context_state}</div>
                              {(i.status === "unfinished" || i.status === "marker") && <button className="btn sm" style={{ marginTop: 4 }} onClick={() => setNoteFor(i)}>Add note</button>}
                            </div>
                          </div>
                          <span />
                        </div>
                      );
                    if (i.kind === "pause")
                      return <div key={i.id} className="tl-row pause"><span className="tl-time">{fmtOffset(i.offset_ms)}</span><span>{i.reason === "restart" ? "App closed — not recording" : "Paused"}{i.end_ms != null ? ` until ${fmtOffset(i.end_ms)}` : ""}</span><span /></div>;
                    return <div key={i.id} className="tl-row event"><span className="tl-time">{fmtOffset(i.offset_ms)}</span><span>{i.source_label}: {i.message || i.event}</span><span /></div>;
                  })}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="stack-lg">
          <OrganisePanel d={s} aiEnabled={!!state?.ai_enabled} />
          {!s.is_demo && <DangerZone d={s} />}
          {sel.size === 0 && tab === "timeline" && <p className="hint"><Check size={13} /> Tip: correcting a misheard word keeps the original text and all card links.</p>}
        </div>
      </div>
      {creating && <CreateCardModal sessionId={s.id} projectId={s.project_id} segs={selectedItems} onClose={() => { setCreating(false); setSel(new Set()); }} />}
      {noteFor && <CaptureNoteModal capture={noteFor} onClose={() => setNoteFor(null)} />}
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}
