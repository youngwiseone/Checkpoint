import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X, Pencil, Undo2, Merge, Scissors, Plus, Play, StopCircle, Link2, Unlink, ImagePlus, Inbox, AlertTriangle, HelpCircle, Sparkles, ExternalLink } from "lucide-react";
import { api, qs } from "../api";
import type { Draft, Evidence, ItemType, TimelineItem } from "../types";
import {
  Banner, Empty, Lightbox, Modal, SaveIndicator, Spinner, TYPES, TYPE_LABEL, Toggle, TypeBadge, fmtOffset, isTypingTarget, useAutosave, useToast,
} from "../components/ui";
import { useProjects } from "../state";

type Tab = "pending" | "approved" | "dismissed";

function Indicators({ d }: { d: Draft }) {
  return (
    <>
      {d.needs_context && <span className="badge warn"><HelpCircle size={12} /> Needs context</span>}
      {d.uncertainty && <span className="badge warn" title={d.uncertainty}><AlertTriangle size={12} /> Uncertain</span>}
      {d.possibly_completed && <span className="badge success" title="The conversation says this was done — confirm before marking it Done."><Sparkles size={12} /> Possibly completed — confirm</span>}
      {d.withdrawn && <span className="badge warn">Withdrawn in conversation</span>}
      {d.conflict_note && d.origin === "ai" && <span className="badge danger" title={d.conflict_note}>Conflicting accounts</span>}
      {d.statement_kind === "idea" && d.type !== "idea" && <span className="badge neutral">Speculative</span>}
      {d.statement_kind === "completed" && <span className="badge neutral">Reported as done</span>}
    </>
  );
}

function sourceSummary(ev: Evidence[]): string {
  const segs = ev.filter((e) => e.kind === "segment" && e.role !== "withdrawal").length;
  const notes = ev.filter((e) => e.kind === "note").length;
  const parts = [];
  if (notes) parts.push(notes === 1 ? "typed note" : `${notes} notes`);
  if (segs) parts.push(`${segs} transcript line${segs === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

function ReviewCard({ d, focused, selected, onFocus, onSelect, onKeep, onEdit, onDismiss, onRestore }: {
  d: Draft; focused: boolean; selected: boolean; onFocus: () => void; onSelect: () => void; onKeep: () => void; onEdit: () => void; onDismiss: () => void; onRestore: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "nearest" });
  }, [focused]);
  const caps = d.evidence.filter((e) => e.kind === "capture");
  const uncertainCaps = caps.some((c) => c.confidence !== "direct");
  return (
    <div ref={ref} className={`rcard ${focused ? "focused" : ""} ${selected ? "selected" : ""}`} onClick={onFocus} role="listitem" aria-current={focused}>
      <input type="checkbox" className="check" checked={selected} onChange={onSelect} onClick={(e) => e.stopPropagation()} aria-label={`Select “${d.title}”`} style={{ width: 17, height: 17, marginTop: 4, accentColor: "var(--accent)" }} />
      <div style={{ minWidth: 0 }}>
        <div className="row wrap" style={{ gap: 8 }}>
          <TypeBadge type={d.type} />
          <span className="title" style={{ flex: 1, minWidth: 200 }}>{d.title}</span>
        </div>
        {d.description && d.description !== d.title && <div className="desc">{d.description}</div>}
        <div className="meta">
          {d.offset_ms != null && <span className="mono">{fmtOffset(d.offset_ms)}</span>}
          {d.session_title && <span className="truncate" style={{ maxWidth: 260 }}>{d.session_title}</span>}
          {sourceSummary(d.evidence) && <span>{sourceSummary(d.evidence)}</span>}
          {d.origin === "ai" && <span>AI suggestion</span>}
          {d.origin === "demo" && <span className="badge accent">Demo</span>}
          <Indicators d={d} />
        </div>
      </div>
      <div className="col" style={{ alignItems: "flex-end", gap: 8 }}>
        {caps.length > 0 && (
          <div className="thumbs">
            {caps.slice(0, 2).map((c) => (
              <img key={c.id} src={c.thumb_url} className={`thumb ${c.confidence !== "direct" ? "uncertain" : ""}`} alt="Screenshot"
                title={c.confidence === "direct" ? "Screenshot" : c.confidence === "uncertain" ? "Possibly related — overlaps several cards" : "Possibly related (taken nearby)"}
                onClick={(e) => { e.stopPropagation(); setZoom(c.image_url!); }} />
            ))}
            {caps.length > 2 && <span className="muted small">+{caps.length - 2}</span>}
          </div>
        )}
        {uncertainCaps && <span className="small" style={{ color: "var(--warn)" }}>{caps.some((c) => c.confidence === "uncertain") ? "Screenshot may belong to another card" : "Screenshot possibly related"}</span>}
        <div className="actions">
          {d.review_state === "pending" && <>
            <button className="btn sm primary" onClick={(e) => { e.stopPropagation(); onKeep(); }} title="Keep (A)"><Check size={15} /> Keep</button>
            <button className="btn sm" onClick={(e) => { e.stopPropagation(); onEdit(); }} title="Edit (E)"><Pencil size={14} /> Edit</button>
            <button className="btn sm" onClick={(e) => { e.stopPropagation(); onDismiss(); }} title="Dismiss (D)"><X size={15} /> Dismiss</button>
          </>}
          {d.review_state === "approved" && <>
            <span className="badge success"><Check size={12} /> Approved</span>
            <button className="btn sm" onClick={(e) => { e.stopPropagation(); onEdit(); }}><Pencil size={14} /> Open</button>
          </>}
          {d.review_state === "dismissed" && <>
            <button className="btn sm" onClick={(e) => { e.stopPropagation(); onEdit(); }}>Open</button>
            <button className="btn sm" onClick={(e) => { e.stopPropagation(); onRestore(); }}><Undo2 size={14} /> Restore</button>
          </>}
        </div>
      </div>
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}

function EvidenceView({ d, onChange }: { d: Draft; onChange: () => void }) {
  const [zoom, setZoom] = useState<string | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [attach, setAttach] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const toast = useToast();
  const detach = useMutation({ mutationFn: (lid: string) => api.del(`/api/drafts/${d.id}/evidence/${lid}`), onSuccess: onChange, onError: (e: Error) => toast("error", e.message) });
  const confirmImg = useMutation({ mutationFn: (cid: string) => api.post(`/api/drafts/${d.id}/attach`, { capture_id: cid }), onSuccess: onChange, onError: (e: Error) => toast("error", e.message) });
  useEffect(() => () => audio.current?.pause(), []);
  const play = (e: Evidence) => {
    if (!e.audio_url) return;
    audio.current?.pause();
    if (playing === e.id) { setPlaying(null); return; }
    const a = new Audio(e.audio_url);
    audio.current = a;
    a.onended = () => setPlaying(null);
    a.onerror = () => { setPlaying(null); toast("error", "This audio isn't available any more."); };
    void a.play();
    setPlaying(e.id);
  };
  const caps = d.evidence.filter((e) => e.kind === "capture");
  const texts = d.evidence.filter((e) => e.kind !== "capture");
  return (
    <div className="stack-lg">
      <div>
        <div className="row"><span className="section-label grow" style={{ margin: 0 }}>Screenshots</span>
          {d.session_id && <button className="btn sm ghost" onClick={() => setAttach(true)}><ImagePlus size={14} /> Attach screenshot</button>}</div>
        {!caps.length ? <p className="muted small" style={{ marginTop: 6 }}>No screenshots attached.</p> : (
          <div className="stack" style={{ marginTop: 8 }}>
            {caps.map((c) => (
              <figure key={c.id} style={{ margin: 0 }}>
                <img src={c.image_url} className={`thumb lg ${c.confidence !== "direct" ? "uncertain" : ""}`} alt={`Screenshot at ${fmtOffset(c.offset_ms)}`} onClick={() => setZoom(c.image_url!)} />
                <figcaption className="row small muted" style={{ marginTop: 4 }}>
                  <span className="mono">{fmtOffset(c.offset_ms)}</span>
                  <span className="grow truncate">{c.window_title}</span>
                  {c.confidence !== "direct" && <span style={{ color: "var(--warn)" }}>{c.confidence === "uncertain" ? "Possibly related — also near other cards" : "Possibly related — taken nearby"}</span>}
                  {c.confidence !== "direct" && <button className="btn sm" onClick={() => confirmImg.mutate(c.capture_id!)}><Link2 size={13} /> Confirm</button>}
                  <button className="btn sm ghost" onClick={() => detach.mutate(c.id)}><Unlink size={13} /> Detach</button>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
        <p className="hint" style={{ marginTop: 6 }}>Screenshots are evidence for you — the local AI reads text only and never looks at images.</p>
      </div>
      <div>
        <div className="section-label">Source</div>
        {!texts.length ? <p className="muted small">No linked text.</p> : (
          <div className="stack">
            {texts.map((e) => (
              <div key={e.id} className={`quote ${e.role === "withdrawal" ? "withdrawal" : ""}`}>
                <div className="who row" style={{ gap: 6 }}>
                  <span className="mono">{fmtOffset(e.offset_ms)}</span>
                  <span>{e.kind === "note" ? (e.note_kind === "quick" ? "Quick note" : "Typed note") : e.source_label}</span>
                  {e.role === "withdrawal" && <span className="badge warn">Withdrawn here</span>}
                  {e.role === "context" && <span className="badge neutral">Context</span>}
                  {e.corrected && <span title={`Recognised as: ${e.raw_text}`}>(corrected)</span>}
                  {e.low_confidence && <span title="Low recognition confidence">· low confidence</span>}
                  <span className="spacer" />
                  {e.audio_url && <button className="icon-btn" onClick={() => play(e)} aria-label={playing === e.id ? "Stop" : "Play audio"}>{playing === e.id ? <StopCircle size={15} /> : <Play size={15} />}</button>}
                  {e.kind !== "capture" && <button className="icon-btn" onClick={() => detach.mutate(e.id)} aria-label="Unlink this source" title="Unlink"><Unlink size={14} /></button>}
                </div>
                <div className="pre">{e.text}</div>
              </div>
            ))}
          </div>
        )}
        {d.session_id && <Link to={`/sessions/${d.session_id}`} className="small row" style={{ marginTop: 8, display: "inline-flex" }}><ExternalLink size={13} /> Open session timeline</Link>}
      </div>
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
      {attach && d.session_id && <AttachModal draft={d} onClose={() => { setAttach(false); onChange(); }} />}
    </div>
  );
}

function AttachModal({ draft, onClose }: { draft: Draft; onClose: () => void }) {
  const tl = useQuery({ queryKey: ["timeline", draft.session_id], queryFn: () => api.get<{ items: TimelineItem[] }>(`/api/sessions/${draft.session_id}/timeline`) });
  const toast = useToast();
  const attach = useMutation({ mutationFn: (cid: string) => api.post(`/api/drafts/${draft.id}/attach`, { capture_id: cid }), onSuccess: onClose, onError: (e: Error) => toast("error", e.message) });
  const have = new Set(draft.evidence.filter((e) => e.kind === "capture").map((e) => e.capture_id));
  const caps = (tl.data?.items ?? []).filter((i) => i.kind === "capture" && !have.has(i.id));
  return (
    <Modal title="Attach a screenshot from this session" onClose={onClose} wide>
      {tl.isLoading ? <Spinner /> : !caps.length ? <p className="muted">No other screenshots in this session.</p> : (
        <div className="row wrap" style={{ gap: 12 }}>
          {caps.map((c) => (
            <button key={c.id} className="btn ghost" style={{ flexDirection: "column", padding: 6 }} onClick={() => attach.mutate(c.id)}>
              <img src={c.thumb_url} className="thumb" style={{ width: 200, height: 112 }} alt={`Screenshot at ${fmtOffset(c.offset_ms)}`} />
              <span className="small mono">{fmtOffset(c.offset_ms)}</span>
            </button>
          ))}
        </div>
      )}
    </Modal>
  );
}

function SplitModal({ d, onClose }: { d: Draft; onClose: () => void }) {
  const [parts, setParts] = useState(() => [
    { title: d.title, description: d.description, type: d.type, ev: d.evidence.map((e) => e.id) },
    { title: "", description: "", type: d.type, ev: d.evidence.map((e) => e.id) },
  ]);
  const qc = useQueryClient();
  const toast = useToast();
  const split = useMutation({
    mutationFn: () => api.post(`/api/drafts/${d.id}/split`, { parts: parts.map((p) => ({ title: p.title, description: p.description, type: p.type, evidence_link_ids: p.ev })) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["drafts"] }); toast("success", `Split into ${parts.length} cards`); onClose(); },
    onError: (e: Error) => toast("error", e.message),
  });
  const upd = (i: number, patch: Partial<(typeof parts)[number]>) => setParts((ps) => ps.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  return (
    <Modal title="Split into separate cards" onClose={onClose} wide footer={
      <><button className="btn" onClick={() => setParts((ps) => [...ps, { title: "", description: "", type: d.type, ev: d.evidence.map((e) => e.id) }])}><Plus size={14} /> Add part</button>
        <span className="spacer" /><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={parts.some((p) => !p.title.trim()) || split.isPending} onClick={() => split.mutate()}>Split</button></>}>
      <div className="grid-2">
        {parts.map((p, i) => (
          <div key={i} className="card stack">
            <div className="row"><b className="grow">Part {i + 1}</b>
              <select className="select" style={{ width: 150 }} value={p.type} onChange={(e) => upd(i, { type: e.target.value as ItemType })} aria-label="Type">{TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}</select>
              {parts.length > 2 && <button className="icon-btn" aria-label="Remove part" onClick={() => setParts((ps) => ps.filter((_, j) => j !== i))}><X size={15} /></button>}</div>
            <input className="input" placeholder="Title" value={p.title} onChange={(e) => upd(i, { title: e.target.value })} aria-label={`Part ${i + 1} title`} />
            <textarea className="textarea" rows={3} placeholder="Description" value={p.description} onChange={(e) => upd(i, { description: e.target.value })} aria-label={`Part ${i + 1} description`} />
            <div className="section-label" style={{ margin: 0 }}>Evidence to keep</div>
            {d.evidence.map((e) => (
              <label key={e.id} className="check small" style={{ display: "flex" }}>
                <input type="checkbox" checked={p.ev.includes(e.id)} onChange={(ev) => upd(i, { ev: ev.target.checked ? [...p.ev, e.id] : p.ev.filter((x) => x !== e.id) })} />
                <span className="truncate">{e.kind === "capture" ? `Screenshot ${fmtOffset(e.offset_ms)}` : `[${fmtOffset(e.offset_ms)}] ${e.text}`}</span>
              </label>
            ))}
          </div>
        ))}
      </div>
    </Modal>
  );
}

function CardDrawer({ id, onClose, onApprove, onDismiss, onRestore }: { id: string; onClose: () => void; onApprove: (id: string) => void; onDismiss: (id: string) => void; onRestore: (id: string) => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["draft", id], queryFn: () => api.get<Draft>(`/api/drafts/${id}`) });
  const [form, setForm] = useState<{ type: ItemType; title: string; description: string; needs_context: boolean } | null>(null);
  const [split, setSplit] = useState(false);
  useEffect(() => {
    if (q.data && (!form || q.data.id !== id)) setForm({ type: q.data.type, title: q.data.title, description: q.data.description, needs_context: q.data.needs_context });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data?.id]);
  const saver = useAutosave(async (v: NonNullable<typeof form>) => {
    if (!v.title.trim()) throw new Error("Title can't be empty");
    await api.patch<Draft>(`/api/drafts/${id}`, v);
    qc.invalidateQueries({ queryKey: ["drafts"] });
  });
  const change = (patch: Partial<NonNullable<typeof form>>) => {
    const next = { ...form!, ...patch };
    setForm(next);
    saver.schedule(next);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector(".overlay, .lightbox")) { e.preventDefault(); void saver.flush(); onClose(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, saver]);
  const d = q.data;
  return (
    <div className="drawer" role="dialog" aria-label="Card details">
      <div className="modal-head">
        <h2 className="grow">Card</h2>
        <SaveIndicator state={saver.state} error={saver.error} onRetry={() => form && saver.schedule(form)} />
        <button className="icon-btn" onClick={() => { void saver.flush(); onClose(); }} aria-label="Close"><X size={18} /></button>
      </div>
      <div className="drawer-body">
        {!d || !form ? <Spinner /> : (
          <div className="stack-lg">
            {d.withdrawn && d.review_state === "dismissed" && (
              <Banner kind="warn" action={<button className="btn sm" onClick={() => onRestore(d.id)}><Undo2 size={14} /> Restore deliberately</button>}>
                <p><b>Withdrawn later in the conversation.</b> It won't become an item unless you restore it.</p>
              </Banner>
            )}
            {d.uncertainty && <Banner kind="warn"><p><b>Uncertain:</b> {d.uncertainty}</p></Banner>}
            {d.conflict_note && d.origin === "ai" && <Banner kind="error"><p><b>Conflicting accounts:</b> {d.conflict_note}</p></Banner>}
            {d.possibly_completed && <Banner kind="success"><p><b>Possibly completed.</b> The source explicitly says this was done. Approving keeps it Open — mark it Done in Project items once you've confirmed.</p></Banner>}
            <div className="row">
              <select className="select" style={{ width: 170 }} value={form.type} onChange={(e) => change({ type: e.target.value as ItemType })} aria-label="Type">
                {TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
              </select>
              <span className="spacer" />
              {d.review_state === "pending" && <span className="badge accent">To review</span>}
              {d.review_state === "approved" && <span className="badge success">Approved</span>}
              {d.review_state === "dismissed" && <span className="badge neutral">Dismissed</span>}
            </div>
            <div className="field"><label htmlFor="dt">Title</label>
              <input id="dt" className="input title-input" value={form.title} onChange={(e) => change({ title: e.target.value })} maxLength={300} /></div>
            <div className="field"><label htmlFor="dd">Description</label>
              <textarea id="dd" className="textarea" rows={5} value={form.description} onChange={(e) => change({ description: e.target.value })} /></div>
            <Toggle checked={form.needs_context} onChange={(v) => change({ needs_context: v })} label="Needs more context" />
            <div className="row wrap">
              {d.review_state === "pending" && <>
                <button className="btn primary" onClick={async () => { await saver.flush(); onApprove(d.id); }}><Check size={15} /> Keep (approve)</button>
                <button className="btn" onClick={() => onDismiss(d.id)}><X size={15} /> Dismiss</button>
                <button className="btn" onClick={() => setSplit(true)}><Scissors size={14} /> Split…</button>
              </>}
              {d.review_state === "dismissed" && !d.withdrawn && <button className="btn" onClick={() => onRestore(d.id)}><Undo2 size={14} /> Restore</button>}
              {d.review_state === "approved" && <Link to="/items" className="btn"><ExternalLink size={14} /> View in Project items</Link>}
            </div>
            <hr className="divider" />
            <EvidenceView d={d} onChange={() => { q.refetch(); qc.invalidateQueries({ queryKey: ["drafts"] }); }} />
          </div>
        )}
      </div>
      {split && d && <SplitModal d={d} onClose={() => { setSplit(false); onClose(); }} />}
    </div>
  );
}

function NewCardModal({ onClose, defaultProject }: { onClose: () => void; defaultProject?: string }) {
  const { data: projects } = useProjects();
  const [pid, setPid] = useState(defaultProject ?? "");
  const [type, setType] = useState<ItemType>("task");
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const qc = useQueryClient();
  const toast = useToast();
  useEffect(() => { if (!pid && projects?.length) setPid(projects.filter((p) => !p.is_demo)[0]?.id ?? projects[0].id); }, [projects, pid]);
  const create = useMutation({
    mutationFn: () => api.post("/api/drafts", { project_id: pid, type, title, description: desc }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["drafts"] }); onClose(); },
    onError: (e: Error) => toast("error", e.message),
  });
  return (
    <Modal title="New card" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!pid || !title.trim()} onClick={() => create.mutate()}>Create</button></>}>
      <div className="stack">
        <div className="grid-2">
          <div className="field"><label htmlFor="np">Project</label><select id="np" className="select" value={pid} onChange={(e) => setPid(e.target.value)}>{projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
          <div className="field"><label htmlFor="ntype">Type</label><select id="ntype" className="select" value={type} onChange={(e) => setType(e.target.value as ItemType)}>{TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}</select></div>
        </div>
        <div className="field"><label htmlFor="nt">Title</label><input id="nt" className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        <div className="field"><label htmlFor="nd">Description</label><textarea id="nd" className="textarea" value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
      </div>
    </Modal>
  );
}

export default function Review() {
  const [params, setParams] = useSearchParams();
  const sessionFilter = params.get("session") ?? undefined;
  const [tab, setTab] = useState<Tab>("pending");
  const [focus, setFocus] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [undoStack, setUndoStack] = useState<{ id: string; title: string }[]>([]);
  const [confirmBulk, setConfirmBulk] = useState(false);
  const [newCard, setNewCard] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({
    queryKey: ["drafts", tab, sessionFilter],
    queryFn: () => api.get<Draft[]>(`/api/drafts${qs({ state: tab, session_id: sessionFilter })}`),
    refetchInterval: 8000,
  });
  const counts = useQuery({ queryKey: ["counts"], queryFn: () => api.get<{ pending: number; approved: number; dismissed: number; unfinished_captures: number }>("/api/review/counts"), refetchInterval: 5000 });
  const drafts = useMemo(() => q.data ?? [], [q.data]);
  useEffect(() => { setFocus((f) => Math.min(f, Math.max(0, drafts.length - 1))); }, [drafts.length]);
  useEffect(() => { setSelected(new Set()); setFocus(0); }, [tab, sessionFilter]);

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["drafts"] });
    qc.invalidateQueries({ queryKey: ["counts"] });
    qc.invalidateQueries({ queryKey: ["state"] });
    qc.invalidateQueries({ queryKey: ["items"] });
  }, [qc]);
  const approve = useMutation({
    mutationFn: (ids: string[]) => api.post<{ approved: unknown[] }>("/api/drafts/approve", { ids }),
    onSuccess: (r, ids) => { refresh(); setSelected(new Set()); toast("success", ids.length === 1 ? "Approved — added to Project items (status Open)" : `Approved ${r.approved.length} cards`); },
    onError: (e: Error) => toast("error", e.message),
  });
  const dismiss = useMutation({
    mutationFn: (d: { id: string; title: string }) => api.post(`/api/drafts/${d.id}/dismiss`),
    onSuccess: (_r, d) => { refresh(); setUndoStack((s) => [...s.slice(-9), d]); },
    onError: (e: Error) => toast("error", e.message),
  });
  const restore = useMutation({
    mutationFn: (id: string) => api.post(`/api/drafts/${id}/restore`),
    onSuccess: () => { refresh(); toast("info", "Restored to review"); },
    onError: (e: Error) => toast("error", e.message),
  });
  const merge = useMutation({
    mutationFn: (ids: string[]) => api.post("/api/drafts/merge", { ids }),
    onSuccess: () => { refresh(); setSelected(new Set()); toast("success", "Merged — sources from all cards are kept"); },
    onError: (e: Error) => toast("error", e.message),
  });
  const undo = useCallback(() => {
    const last = undoStack[undoStack.length - 1];
    if (!last) return;
    setUndoStack((s) => s.slice(0, -1));
    restore.mutate(last.id);
  }, [undoStack, restore]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (open || e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(document.activeElement) || document.querySelector(".overlay, .lightbox")) return;
      const d = drafts[focus];
      const k = e.key.toLowerCase();
      if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); setFocus((f) => Math.min(drafts.length - 1, f + 1)); }
      else if (k === "k" || e.key === "ArrowUp") { e.preventDefault(); setFocus((f) => Math.max(0, f - 1)); }
      else if (k === "u") { e.preventDefault(); undo(); }
      else if (!d) return;
      else if (k === "a" && d.review_state === "pending") { e.preventDefault(); approve.mutate([d.id]); }
      else if (k === "d" && d.review_state === "pending") { e.preventDefault(); dismiss.mutate({ id: d.id, title: d.title }); }
      else if (k === "e" || e.key === "Enter") { e.preventDefault(); setOpen(d.id); }
      else if (k === "x" || e.key === " ") { e.preventDefault(); setSelected((s) => { const n = new Set(s); if (n.has(d.id)) n.delete(d.id); else n.add(d.id); return n; }); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drafts, focus, open, approve, dismiss, undo]);

  const sel = drafts.filter((d) => selected.has(d.id));
  const selPending = sel.filter((d) => d.review_state === "pending");
  const lastUndo = undoStack[undoStack.length - 1];

  return (
    <div className="page wide">
      <div className="page-head">
        <div className="grow">
          <h1>Review inbox</h1>
          <p>{counts.data ? (counts.data.pending ? `${counts.data.pending} card${counts.data.pending === 1 ? "" : "s"} left to review` : "All caught up") : " "}
            {sessionFilter && <> · filtered to one session <button className="btn ghost sm" onClick={() => setParams({})}>Show all</button></>}</p>
        </div>
        <button className="btn" onClick={() => setNewCard(true)}><Plus size={15} /> New card</button>
      </div>
      {!!counts.data?.unfinished_captures && (
        <div style={{ marginBottom: 16 }}>
          <Banner kind="info" action={<Link to="/" className="btn sm">Open inbox</Link>}>
            {counts.data.unfinished_captures} unfinished capture{counts.data.unfinished_captures === 1 ? "" : "s"} kept for later — add context or discard them from Projects & sessions.
          </Banner>
        </div>
      )}
      <div className="tabs" role="tablist">
        {(["pending", "approved", "dismissed"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "pending" ? "To review" : t === "approved" ? "Approved" : "Dismissed & withdrawn"}
            {counts.data && <span className="muted"> {counts.data[t]}</span>}
          </button>
        ))}
      </div>
      <div className="row wrap" style={{ marginBottom: 12, minHeight: 36 }}>
        {selected.size > 0 ? (
          <>
            <b>{selected.size} selected</b>
            {selPending.length > 0 && <button className="btn sm primary" onClick={() => setConfirmBulk(true)}><Check size={14} /> Approve {selPending.length} selected</button>}
            {selPending.length >= 2 && <button className="btn sm" onClick={() => merge.mutate(selPending.map((d) => d.id))}><Merge size={14} /> Merge {selPending.length}</button>}
            <button className="btn sm ghost" onClick={() => setSelected(new Set())}>Clear selection</button>
          </>
        ) : (
          <div className="kbd-hints">
            <span><kbd>J</kbd>/<kbd>K</kbd> move</span><span><kbd>A</kbd> keep</span><span><kbd>E</kbd> edit</span><span><kbd>D</kbd> dismiss</span><span><kbd>U</kbd> undo</span><span><kbd>X</kbd> select</span>
          </div>
        )}
        <span className="spacer" />
        {lastUndo && <span className="row small text-2">Dismissed “{lastUndo.title.slice(0, 40)}{lastUndo.title.length > 40 ? "…" : ""}” <button className="btn sm" onClick={undo}><Undo2 size={13} /> Undo</button></span>}
      </div>
      {q.isLoading ? <Spinner label="Loading cards…" /> : q.error ? <Banner kind="error">{(q.error as Error).message}</Banner> : !drafts.length ? (
        <div className="card"><Empty icon={<Inbox size={34} />} title={tab === "pending" ? "Nothing to review" : tab === "approved" ? "No approved cards yet" : "Nothing dismissed"}>
          {tab === "pending" ? "Cards appear here from typed notes, F9 quick notes, transcript selections and local AI organisation." : null}
        </Empty></div>
      ) : (
        <div className="review-layout" role="list">
          {drafts.map((d, i) => (
            <ReviewCard key={d.id} d={d} focused={i === focus} selected={selected.has(d.id)} onFocus={() => setFocus(i)}
              onSelect={() => setSelected((s) => { const n = new Set(s); if (n.has(d.id)) n.delete(d.id); else n.add(d.id); return n; })}
              onKeep={() => approve.mutate([d.id])} onEdit={() => { setFocus(i); setOpen(d.id); }}
              onDismiss={() => dismiss.mutate({ id: d.id, title: d.title })} onRestore={() => restore.mutate(d.id)} />
          ))}
        </div>
      )}
      {open && <CardDrawer id={open} onClose={() => { setOpen(null); refresh(); }}
        onApprove={(id) => { approve.mutate([id]); setOpen(null); }}
        onDismiss={(id) => { const d = drafts.find((x) => x.id === id); dismiss.mutate({ id, title: d?.title ?? "card" }); setOpen(null); }}
        onRestore={(id) => { restore.mutate(id); setOpen(null); }} />}
      {confirmBulk && (
        <Modal title={`Approve ${selPending.length} card${selPending.length === 1 ? "" : "s"}?`} onClose={() => setConfirmBulk(false)} footer={
          <><button className="btn" onClick={() => setConfirmBulk(false)}>Cancel</button>
            <button className="btn primary" onClick={() => { setConfirmBulk(false); approve.mutate(selPending.map((d) => d.id)); }}>Approve {selPending.length}</button></>}>
          <p>Only these selected cards will be approved and added to Project items with status Open:</p>
          <ul>{selPending.map((d) => <li key={d.id}>{d.title}</li>)}</ul>
          <p className="muted small">Approving doesn't mark work as done and doesn't share anything.</p>
        </Modal>
      )}
      {newCard && <NewCardModal onClose={() => setNewCard(false)} />}
    </div>
  );
}
