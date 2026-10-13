import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, Plus, X, Image as ImageIcon, Crop } from "lucide-react";
import { api, qs } from "../api";
import type { ItemType, WorkItem, WorkStatus } from "../types";
import { Empty, Lightbox, Modal, SaveIndicator, Spinner, TYPES, TYPE_LABEL, WORK_LABEL, fmtDate, fmtOffset, useAutosave, useToast } from "../components/ui";
import { useCurrentProject } from "../state";
import ScreenshotEditor from "../components/ScreenshotEditor";

function ItemDrawer({ id, onClose, previous, next }: { id: string; onClose: () => void; previous?: () => void; next?: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["item", id], queryFn: () => api.get<WorkItem>(`/api/items/${id}`), refetchInterval: 5000 });
  type F = { type: ItemType; description: string; work_status: WorkStatus; assignee: string; priority: string; tags: string };
  const [form, setForm] = useState<F | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [cropId, setCropId] = useState<string | null>(null);
  useEffect(() => {
    const d = q.data;
    if (d && (!form || saver.state === "idle" || saver.state === "saved"))
      setForm({ type: d.type, description: d.description, work_status: d.work_status, assignee: d.assignee ?? "", priority: d.priority ?? "", tags: (d.tags ?? []).join(", ") });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data?.version, q.data?.id]);
  const saver = useAutosave(async (v: F) => {
    await api.patch(`/api/items/${id}`, { ...v, assignee: v.assignee || null, priority: v.priority || null, tags: v.tags.split(",").map((t) => t.trim()).filter(Boolean) });
    qc.invalidateQueries({ queryKey: ["items"] });
    qc.invalidateQueries({ queryKey: ["item", id] });
  });
  const change = (patch: Partial<F>) => { const n = { ...form!, ...patch }; setForm(n); saver.schedule(n); };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector(".overlay, .lightbox")) { void saver.flush(); onClose(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, saver]);
  const d = q.data;
  const captures = (d?.evidence ?? []).filter((e) => e.kind === "capture");
  const capture = captures[Math.min(frameIndex, Math.max(0, captures.length - 1))];
  return (
    <div className="drawer evidence-drawer" role="dialog" aria-label="Item details">
      <div className="modal-head">
        <h2 className="grow">Review task</h2>
        <button className="btn sm" disabled={!previous || saver.state === "saving"} onClick={async () => { if (await saver.flush()) previous?.(); }}>Previous</button>
        <button className="btn sm" disabled={!next || saver.state === "saving"} onClick={async () => { if (await saver.flush()) next?.(); }}>Next</button>
        <SaveIndicator state={saver.state} error={saver.error} onRetry={() => form && saver.schedule(form)} />
        <button className="icon-btn" onClick={() => { void saver.flush(); onClose(); }} aria-label="Close"><X size={18} /></button>
      </div>
      <div className="drawer-body">
        {!d || !form ? <Spinner /> : (
          <div className="stack-lg">
            {capture && <figure className="review-screenshot">
              <img src={capture.image_url} alt="Screenshot evidence" onClick={() => setZoom(capture.image_url!)} />
              <figcaption className="row wrap small muted">
                <span>{fmtOffset(capture.offset_ms)}{capture.confidence !== "direct" ? " · possibly related" : ""}</span>
                <span className="spacer" />
                {capture.capture_id && <button className="btn sm" onClick={() => setCropId(capture.capture_id!)}><Crop size={14} /> Edit screenshot</button>}
              </figcaption>
              {captures.length > 1 && <div className="review-strip">{captures.map((c, index) => <button key={c.id} className={`btn ${index === frameIndex ? "primary" : ""}`} aria-label={`Screenshot ${index + 1}`} aria-pressed={index === frameIndex} onClick={() => setFrameIndex(index)}><img src={c.thumb_url ?? c.image_url} alt={`Screenshot ${index + 1}`} /></button>)}</div>}
            </figure>}
            {d.origin === "shared" && (d.remote_attachments ?? []).map((a) => (
              <img key={a.id} src={a.url} className="thumb lg" alt="Shared screenshot" onClick={() => setZoom(a.url)} />
            ))}
            {(d.evidence ?? []).filter((e) => e.kind !== "capture").map((e) => (
              <div key={e.id} className={`quote ${e.role === "withdrawal" ? "withdrawal" : ""}`}>
                <div className="who">{fmtOffset(e.offset_ms)} · {e.kind === "note" ? "Typed note" : e.source_label}{e.role === "withdrawal" ? " · withdrawn here" : ""}</div>
                <div className="pre">{e.text}</div>
              </div>
            ))}
            <div className="row wrap">
              <select className="select" style={{ width: 160 }} value={form.type} onChange={(e) => change({ type: e.target.value as ItemType })} aria-label="Type">{TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}</select>
              <select className="select" style={{ width: 160 }} value={form.work_status} onChange={(e) => change({ work_status: e.target.value as WorkStatus })} aria-label="Work status">{(["open", "done", "wont_do"] as const).map((s) => <option key={s} value={s}>{WORK_LABEL[s]}</option>)}</select>
              <span className="spacer" />
            </div>
            {d.sync_error && d.sharing_state !== "synced" && <p className="small" style={{ color: d.sharing_state === "failed" ? "var(--danger)" : "var(--text-2)" }}>{d.sync_error}</p>}
            <div className="field"><label htmlFor="id">Note</label><textarea id="id" className="textarea" rows={5} value={form.description} onChange={(e) => change({ description: e.target.value })} /></div>
            <details>
              <summary className="label" style={{ cursor: "pointer" }}>More fields</summary>
              <div className="grid-2" style={{ marginTop: 10 }}>
                <div className="field"><label htmlFor="ia">Assignee</label><input id="ia" className="input" value={form.assignee} onChange={(e) => change({ assignee: e.target.value })} /></div>
                <div className="field"><label htmlFor="ip">Priority</label>
                  <select id="ip" className="select" value={form.priority} onChange={(e) => change({ priority: e.target.value })}><option value="">None</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></div>
                <div className="field" style={{ gridColumn: "1 / -1" }}><label htmlFor="itag">Tags</label><input id="itag" className="input" value={form.tags} onChange={(e) => change({ tags: e.target.value })} placeholder="comma, separated" /></div>
              </div>
            </details>
            <p className="muted small">
              {d.session_title ? `From “${d.session_title}” · ` : ""}Created {fmtDate(d.created_at)}{d.created_by ? ` by ${d.created_by}` : ""} · updated {fmtDate(d.updated_at)}{d.updated_by ? ` by ${d.updated_by}` : ""} · v{d.version}
              {d.completed_at ? ` · done ${fmtDate(d.completed_at)}` : ""}
            </p>
            <hr className="divider" />
            {d.origin === "shared" && !(d.evidence ?? []).length && <p className="muted small">Shared by another client. Only the fields and screenshots they chose to publish are available.</p>}
          </div>
        )}
      </div>
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
      {cropId && <ScreenshotEditor captureId={cropId} onClose={() => setCropId(null)} />}
    </div>
  );
}

function NewItemModal({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [type, setType] = useState<ItemType>("task");
  const [desc, setDesc] = useState("");
  const qc = useQueryClient();
  const toast = useToast();
  const create = useMutation({ mutationFn: () => api.post("/api/items", { project_id: projectId, type, title: desc.trim().split(/\r?\n/)[0].slice(0, 300), description: desc }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["items"] }); onClose(); }, onError: (e: Error) => toast("error", e.message) });
  return (
    <Modal title="New note" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!desc.trim() || create.isPending} onClick={() => create.mutate()}>Add note</button></>}>
      <div className="stack">
        <div className="field"><label htmlFor="nit">Type</label><select id="nit" className="select" value={type} onChange={(e) => setType(e.target.value as ItemType)} style={{ maxWidth: 220 }}>{TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}</select></div>
        <div className="field"><label htmlFor="nitd">Note</label><textarea id="nitd" className="textarea" value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
      </div>
    </Modal>
  );
}

export default function Items() {
  const [params, setParams] = useSearchParams();
  const [project, setProject] = useCurrentProject();
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState("open");
  const [open, setOpen] = useState<string | null>(params.get("item"));
  const [creating, setCreating] = useState(false);
  const [undoAction, setUndoAction] = useState<{ id: string; status: WorkStatus; version: number } | null>(null);
  useEffect(() => { setUndoAction(null); }, [project?.id]);
  const qc = useQueryClient();
  const toast = useToast();
  useEffect(() => { const p = params.get("project"); if (p) setProject(p); }, [params, setProject]);
  const items = useQuery({ queryKey: ["items", project?.id, search, tab],
    queryFn: () => api.get<WorkItem[]>(`/api/items${qs({ project_id: project!.id, q: search, status: tab === "open" ? ["open", "in_progress"] : tab === "done" ? ["done", "wont_do"] : [] })}`),
    enabled: !!project, refetchInterval: 3000 });
  const status = useMutation({
    mutationFn: ({ id, value, version }: { id: string; value: WorkStatus; version: number; previousStatus: WorkStatus }) => api.patch<WorkItem>(`/api/items/${id}`, { work_status: value, expected_version: version }),
    onSuccess: (updated, variables) => {
      setUndoAction({ id: variables.id, status: variables.previousStatus, version: updated.version });
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item", variables.id] });
    }, onError: (e: Error) => toast("error", e.message)
  });
  const undo = useMutation({
    mutationFn: () => api.patch(`/api/items/${undoAction!.id}`, { work_status: undoAction!.status, expected_version: undoAction!.version }),
    onSuccess: () => { setUndoAction(null); qc.invalidateQueries({ queryKey: ["items"] }); qc.invalidateQueries({ queryKey: ["item"] }); },
    onError: (e: Error) => toast("error", e.message)
  });
  const openIndex = (items.data ?? []).findIndex((item) => item.id === open);
  if (!project) return <div className="page"><Empty title="No project yet">Create a project from the sidebar.</Empty></div>;
  return <div className="page wide">
    <div className="page-head"><div className="grow"><h1>Inbox</h1><p>{project.name} · Capture here, work through it in your chat.</p></div>
      <button className="btn" onClick={() => setCreating(true)}><Plus size={15} /> Add note</button></div>
    {undoAction && <div className="card row" role="status" style={{ marginBottom: 12 }}><span className="grow">Task status updated.</span><button className="btn sm" disabled={status.isPending || undo.isPending} onClick={() => undo.mutate()}>Undo last action</button></div>}
    <div className="tabs" role="tablist">{[["open", "Open"], ["done", "Done / archived"], ["all", "All"]].map(([v, label]) =>
      <button key={v} className={`tab ${tab === v ? "active" : ""}`} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}>{label}</button>)}</div>
    <div className="row" style={{ marginBottom: 16 }}><Search size={16} /><input className="input" aria-label="Search inbox" placeholder="Search captures and notes" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
    {items.isLoading ? <Spinner /> : items.error ? <p role="alert">{(items.error as Error).message}</p> : !items.data?.length ?
      <Empty title="Nothing here yet">Tap your capture key to save a screenshot, double tap to edit, hold to review, tap then hold to dictate, or add a note above.</Empty> :
      <div className="stack">{items.data.map((i) => <div className="card row" key={i.id}>
        {i.thumb_url ? <img src={i.thumb_url} className="thumb" alt="Screenshot" style={{ width: 120, height: 76 }} onClick={() => setOpen(i.id)} /> : <ImageIcon size={20} className="muted" />}
        <button className="btn ghost grow" style={{ textAlign: "left", justifyContent: "flex-start", whiteSpace: "normal" }} onClick={() => setOpen(i.id)}>
          <span><b>{i.description || "No note yet"}</b><span className="small muted" style={{ display: "block" }}>{fmtDate(i.created_at)}{i.capture_count > 1 ? ` · ${i.capture_count} screenshots` : ""}</span></span></button>
        <button className="btn sm" disabled={status.isPending || undo.isPending} onClick={() => status.mutate({ id: i.id, previousStatus: i.work_status, version: i.version, value: i.work_status === "done" || i.work_status === "wont_do" ? "open" : "done" })}>{i.work_status === "done" || i.work_status === "wont_do" ? "Reopen" : "Done"}</button>
        {i.work_status !== "wont_do" && <button className="btn sm ghost" disabled={status.isPending || undo.isPending} onClick={() => status.mutate({ id: i.id, previousStatus: i.work_status, value: "wont_do", version: i.version })}>Archive</button>}
      </div>)}</div>}
    {open && <ItemDrawer key={open} id={open} previous={openIndex > 0 ? () => setOpen(items.data![openIndex - 1].id) : undefined} next={openIndex >= 0 && openIndex < (items.data?.length ?? 0) - 1 ? () => setOpen(items.data![openIndex + 1].id) : undefined} onClose={() => { setOpen(null); if (params.has("item")) { const n = new URLSearchParams(params); n.delete("item"); setParams(n, { replace: true }); } }} />}
    {creating && <NewItemModal projectId={project.id} onClose={() => setCreating(false)} />}
  </div>;
}
