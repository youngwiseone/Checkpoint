import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Search, Share2, RefreshCw, Plus, X, Image as ImageIcon, CloudOff, RotateCw } from "lucide-react";
import { api, download, qs } from "../api";
import type { ItemType, SessionSummary, WorkItem, WorkStatus } from "../types";
import {
  Banner, Empty, Lightbox, Modal, SaveIndicator, ShareBadge, Spinner, TYPES, TYPE_LABEL, TypeBadge, WORK_LABEL, WORK_STATUSES, fmtDate, fmtOffset, useAutosave, useToast,
} from "../components/ui";
import { useProjects } from "../state";

type ShareStatus = { server_url: string; display_name: string; token_saved: boolean; outbox: Record<string, number>; offline_reason: string | null };

function ConflictPanel({ item, onDone }: { item: WorkItem; onDone: () => void }) {
  const toast = useToast();
  const resolve = useMutation({ mutationFn: (keep: "local" | "server") => api.post(`/api/items/${item.id}/resolve`, { keep }), onSuccess: onDone, onError: (e: Error) => toast("error", e.message) });
  const server = (item.conflict_server_copy ?? {}) as Record<string, unknown>;
  const fields: [string, string][] = [["title", "Title"], ["description", "Description"], ["work_status", "Status"], ["type", "Type"], ["assignee", "Assignee"], ["priority", "Priority"]];
  const diff = fields.filter(([k]) => JSON.stringify((item as unknown as Record<string, unknown>)[k] ?? null) !== JSON.stringify(server[k] ?? null));
  return (
    <Banner kind="warn">
      <p><b>Someone else changed this item on the shared server{server.updated_by ? ` (${String(server.updated_by)})` : ""}.</b> Nothing was overwritten. Choose which version to keep.</p>
      <table className="table small" style={{ marginTop: 8 }}>
        <thead><tr><th>Field</th><th>Yours</th><th>Server</th></tr></thead>
        <tbody>{diff.map(([k, label]) => (
          <tr key={k}><td>{label}</td><td className="pre">{String((item as unknown as Record<string, unknown>)[k] ?? "—")}</td><td className="pre">{String(server[k] ?? "—")}</td></tr>
        ))}</tbody>
      </table>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn sm" onClick={() => resolve.mutate("local")}>Keep mine (send to server)</button>
        <button className="btn sm" onClick={() => resolve.mutate("server")}>Keep server version</button>
      </div>
    </Banner>
  );
}

function ItemDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["item", id], queryFn: () => api.get<WorkItem>(`/api/items/${id}`), refetchInterval: 5000 });
  type F = { type: ItemType; title: string; description: string; work_status: WorkStatus; assignee: string; priority: string; tags: string };
  const [form, setForm] = useState<F | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  useEffect(() => {
    const d = q.data;
    if (d && (!form || saver.state === "idle" || saver.state === "saved"))
      setForm({ type: d.type, title: d.title, description: d.description, work_status: d.work_status, assignee: d.assignee ?? "", priority: d.priority ?? "", tags: (d.tags ?? []).join(", ") });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data?.version, q.data?.id]);
  const saver = useAutosave(async (v: F) => {
    if (!v.title.trim()) throw new Error("Title can't be empty");
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
  return (
    <div className="drawer" role="dialog" aria-label="Item details">
      <div className="modal-head">
        <h2 className="grow">Item</h2>
        <SaveIndicator state={saver.state} error={saver.error} onRetry={() => form && saver.schedule(form)} />
        <button className="icon-btn" onClick={() => { void saver.flush(); onClose(); }} aria-label="Close"><X size={18} /></button>
      </div>
      <div className="drawer-body">
        {!d || !form ? <Spinner /> : (
          <div className="stack-lg">
            {d.sharing_state === "conflict" && <ConflictPanel item={d} onDone={() => { q.refetch(); qc.invalidateQueries({ queryKey: ["items"] }); }} />}
            <div className="row wrap">
              <select className="select" style={{ width: 160 }} value={form.type} onChange={(e) => change({ type: e.target.value as ItemType })} aria-label="Type">{TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}</select>
              <select className="select" style={{ width: 160 }} value={form.work_status} onChange={(e) => change({ work_status: e.target.value as WorkStatus })} aria-label="Work status">{WORK_STATUSES.map((s) => <option key={s} value={s}>{WORK_LABEL[s]}</option>)}</select>
              <span className="spacer" />
              <ShareBadge state={d.sharing_state} error={d.sync_error} />
            </div>
            {d.sync_error && d.sharing_state !== "synced" && <p className="small" style={{ color: d.sharing_state === "failed" ? "var(--danger)" : "var(--text-2)" }}>{d.sync_error}</p>}
            <div className="field"><label htmlFor="it">Title</label><input id="it" className="input title-input" value={form.title} onChange={(e) => change({ title: e.target.value })} /></div>
            <div className="field"><label htmlFor="id">Description</label><textarea id="id" className="textarea" rows={5} value={form.description} onChange={(e) => change({ description: e.target.value })} /></div>
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
            {(d.evidence ?? []).filter((e) => e.kind === "capture").map((c) => (
              <figure key={c.id} style={{ margin: 0 }}>
                <img src={c.image_url} className={`thumb lg ${c.confidence !== "direct" ? "uncertain" : ""}`} alt="Screenshot" onClick={() => setZoom(c.image_url!)} />
                <figcaption className="small muted">{fmtOffset(c.offset_ms)}{c.confidence !== "direct" ? " · possibly related" : ""}</figcaption>
              </figure>
            ))}
            {d.origin === "shared" && (d.remote_attachments ?? []).map((a) => (
              <img key={a.id} src={a.url} className="thumb lg" alt="Shared screenshot" onClick={() => setZoom(a.url)} />
            ))}
            {(d.evidence ?? []).filter((e) => e.kind !== "capture").map((e) => (
              <div key={e.id} className={`quote ${e.role === "withdrawal" ? "withdrawal" : ""}`}>
                <div className="who">{fmtOffset(e.offset_ms)} · {e.kind === "note" ? "Typed note" : e.source_label}{e.role === "withdrawal" ? " · withdrawn here" : ""}</div>
                <div className="pre">{e.text}</div>
              </div>
            ))}
            {d.origin === "shared" && !(d.evidence ?? []).length && <p className="muted small">Shared by another client. Only the fields and screenshots they chose to publish are available.</p>}
          </div>
        )}
      </div>
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}

function PublishModal({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const [shots, setShots] = useState(true);
  const [excerpts, setExcerpts] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  type Preview = { server_url: string; display_name: string; items: { id: string; project: string; shared_project: string | null; shareable: boolean; payload: { title: string; type: ItemType; work_status: WorkStatus; description: string; excerpts: { text: string; source: string }[]; attachments: { id: string; size: number }[] }; thumbs: string[] }[] };
  const pv = useQuery({ queryKey: ["preview", ids, shots, excerpts], queryFn: () => api.post<Preview>("/api/share/preview", { ids, include_screenshots: shots, include_excerpts: excerpts }) });
  const publish = useMutation({
    mutationFn: () => api.post<{ queued: number }>("/api/share/publish", { ids, include_screenshots: shots, include_excerpts: excerpts }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["items"] }); toast("success", `${r.queued} item(s) saved locally and queued to sync`); onClose(); },
    onError: (e: Error) => toast("error", e.message),
  });
  const blocked = pv.data?.items.filter((i) => !i.shareable) ?? [];
  const dest = pv.data?.items.find((i) => i.shareable)?.shared_project;
  return (
    <Modal wide title="Publish to shared workspace" onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!pv.data || blocked.length > 0 || publish.isPending} onClick={() => publish.mutate()}><Share2 size={15} /> Publish {ids.length} item{ids.length === 1 ? "" : "s"}</button></>}>
      {!pv.data ? <Spinner /> : (
        <div className="stack">
          <p>Destination: <b>{dest ?? "—"}</b> on <code>{pv.data.server_url || "no server configured"}</code>{pv.data.display_name ? <> · shown as <b>{pv.data.display_name}</b></> : null}</p>
          {blocked.length > 0 && <Banner kind="error">{blocked.length} item(s) belong to a local-only project. Link the project to a shared project first (Project items → Sharing). Nothing from local-only projects is ever uploaded.</Banner>}
          <div className="row wrap" style={{ gap: 20 }}>
            <label className="check"><input type="checkbox" checked={shots} onChange={(e) => setShots(e.target.checked)} /> Include screenshots</label>
            <label className="check"><input type="checkbox" checked={excerpts} onChange={(e) => setExcerpts(e.target.checked)} /> Include linked transcript/note excerpts</label>
          </div>
          <p className="hint">Raw audio and full transcripts always stay on this PC.</p>
          {pv.data.items.map((i) => (
            <div key={i.id} className="card tight">
              <div className="row"><TypeBadge type={i.payload.type} /><b className="grow">{i.payload.title}</b><span className="badge outline">{WORK_LABEL[i.payload.work_status]}</span></div>
              {i.payload.description && <p className="small text-2 pre" style={{ marginTop: 4 }}>{i.payload.description}</p>}
              {i.thumbs.length > 0 && <div className="thumbs" style={{ marginTop: 6 }}>{i.thumbs.map((t) => <img key={t} src={t} className="thumb" alt="Screenshot to upload" />)}</div>}
              {i.payload.excerpts.map((e, k) => <p key={k} className="quote small" style={{ marginTop: 4 }}>{e.source}: {e.text}</p>)}
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

function ExportModal({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const [format, setFormat] = useState<"md" | "json" | "zip">("zip");
  const [shots, setShots] = useState(true);
  const [excerpts, setExcerpts] = useState(true);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const go = async () => {
    setBusy(true);
    try { await download("/api/export", { ids, format, include_screenshots: shots, include_excerpts: excerpts }); onClose(); }
    catch (e) { toast("error", (e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <Modal title={`Export ${ids.length} item${ids.length === 1 ? "" : "s"}`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy} onClick={go}><Download size={15} /> Export</button></>}>
      <div className="stack">
        <div className="seg" role="radiogroup" aria-label="Format">
          {(["zip", "md", "json"] as const).map((f) => <button key={f} role="radio" aria-checked={format === f} className={format === f ? "on" : ""} onClick={() => setFormat(f)}>{f === "zip" ? "ZIP (Markdown + JSON + images)" : f === "md" ? "Markdown" : "JSON"}</button>)}
        </div>
        {format === "zip" && <label className="check"><input type="checkbox" checked={shots} onChange={(e) => setShots(e.target.checked)} /> Include screenshots (relative links)</label>}
        <label className="check"><input type="checkbox" checked={excerpts} onChange={(e) => setExcerpts(e.target.checked)} /> Include linked transcript/note excerpts</label>
        <p className="hint">Exports contain only the selected items and their linked evidence — no audio, settings or tokens. Handy for sharing or giving to a coding assistant.</p>
      </div>
    </Modal>
  );
}

function NewItemModal({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [type, setType] = useState<ItemType>("task");
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const qc = useQueryClient();
  const toast = useToast();
  const create = useMutation({ mutationFn: () => api.post("/api/items", { project_id: projectId, type, title, description: desc }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["items"] }); onClose(); }, onError: (e: Error) => toast("error", e.message) });
  return (
    <Modal title="New item" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!title.trim()} onClick={() => create.mutate()}>Add item</button></>}>
      <div className="stack">
        <div className="field"><label htmlFor="nit">Type</label><select id="nit" className="select" value={type} onChange={(e) => setType(e.target.value as ItemType)} style={{ maxWidth: 220 }}>{TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}</select></div>
        <div className="field"><label htmlFor="nitt">Title</label><input id="nitt" className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        <div className="field"><label htmlFor="nitd">Description</label><textarea id="nitd" className="textarea" value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
      </div>
    </Modal>
  );
}

export default function Items() {
  const [params, setParams] = useSearchParams();
  const { data: projects } = useProjects();
  const projectId = params.get("project") ?? projects?.[0]?.id ?? "";
  const project = projects?.find((p) => p.id === projectId);
  const [search, setSearch] = useState("");
  const [types, setTypes] = useState<ItemType[]>([]);
  const [statuses, setStatuses] = useState<WorkStatus[]>(["open", "in_progress"]);
  const [sessionId, setSessionId] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [modal, setModal] = useState<"publish" | "export" | "new" | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const items = useQuery({
    queryKey: ["items", projectId, search, types, statuses, sessionId],
    queryFn: () => api.get<WorkItem[]>(`/api/items${qs({ project_id: projectId, q: search, type: types, status: statuses, session_id: sessionId })}`),
    enabled: !!projectId,
    refetchInterval: 5000,
  });
  const sessions = useQuery({ queryKey: ["sessions", projectId], queryFn: () => api.get<SessionSummary[]>(`/api/sessions${qs({ project_id: projectId })}`), enabled: !!projectId });
  const share = useQuery({ queryKey: ["share-status"], queryFn: () => api.get<ShareStatus>("/api/share/status"), refetchInterval: 5000 });
  const updateStatus = useMutation({
    mutationFn: ({ id, work_status }: { id: string; work_status: WorkStatus }) => api.patch(`/api/items/${id}`, { work_status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["items"] }),
    onError: (e: Error) => toast("error", e.message),
  });
  const refresh = useMutation({
    mutationFn: () => api.post<{ added: number; updated: number; conflicts: number }>(`/api/projects/${projectId}/refresh`),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["items"] }); qc.invalidateQueries({ queryKey: ["projects"] }); toast("success", `Refreshed: ${r.added} new, ${r.updated} updated${r.conflicts ? `, ${r.conflicts} conflict(s)` : ""}`); },
    onError: (e: Error) => toast("error", e.message),
  });
  const retry = useMutation({ mutationFn: () => api.post<{ requeued: number }>("/api/share/retry"), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["items"] }); toast("info", `Retrying ${r.requeued} item(s)`); } });
  useEffect(() => setSelected(new Set()), [projectId]);
  const list = useMemo(() => items.data ?? [], [items.data]);
  const toggleIn = <T,>(arr: T[], v: T) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const allSelected = list.length > 0 && list.every((i) => selected.has(i.id));
  const failed = share.data?.outbox.failed ?? 0;
  const pending = share.data?.outbox.pending ?? 0;

  return (
    <div className="page wide">
      <div className="page-head">
        <div className="grow">
          <h1>Project items</h1>
          <p>Approved work, tracked locally{project?.shared_project_id ? " and shared with your team" : ""}.</p>
        </div>
        <select className="select" style={{ width: 260 }} value={projectId} onChange={(e) => setParams({ project: e.target.value })} aria-label="Project">
          {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_demo ? " (demo)" : ""}</option>)}
        </select>
        <button className="btn" onClick={() => setModal("new")} disabled={!projectId}><Plus size={15} /> New item</button>
      </div>

      {project?.shared_project_id ? (
        <div className="card tight row wrap" style={{ marginBottom: 16 }}>
          <Share2 size={16} className="muted" /><span className="grow">Shared as <b>{project.shared_project_name}</b>{project.last_refreshed_at ? ` · refreshed ${fmtDate(project.last_refreshed_at)}` : ""}</span>
          {share.data?.offline_reason && <span className="row small" style={{ color: "var(--warn)" }}><CloudOff size={14} /> {share.data.offline_reason} Saved locally — waiting to sync.</span>}
          {pending > 0 && !share.data?.offline_reason && <span className="small text-2">{pending} waiting to sync</span>}
          {failed > 0 && <button className="btn sm" onClick={() => retry.mutate()}><RotateCw size={14} /> Retry {failed} failed</button>}
          <button className="btn sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}><RefreshCw size={14} /> {refresh.isPending ? "Refreshing…" : "Refresh from server"}</button>
        </div>
      ) : project && !project.is_demo ? (
        <p className="muted small" style={{ marginBottom: 12 }}>Local-only project. To share with a team, configure a server in Settings → Sharing and link this project there.</p>
      ) : null}

      <div className="card tight" style={{ marginBottom: 14 }}>
        <div className="row wrap" style={{ gap: 12 }}>
          <div className="row" style={{ flex: "1 1 260px" }}>
            <Search size={16} className="muted" />
            <input className="input" placeholder="Search titles and descriptions" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search items" />
          </div>
          <select className="select" style={{ width: 240 }} value={sessionId} onChange={(e) => setSessionId(e.target.value)} aria-label="Session">
            <option value="">All sessions</option>
            {sessions.data?.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
          </select>
        </div>
        <div className="row wrap" style={{ marginTop: 10, gap: 6 }}>
          <span className="small muted">Type</span>
          {TYPES.map((t) => <button key={t} className={`badge ${types.includes(t) ? `type-${t}` : "outline"}`} style={{ cursor: "pointer" }} aria-pressed={types.includes(t)} onClick={() => setTypes((x) => toggleIn(x, t))}>{TYPE_LABEL[t]}</button>)}
          <span className="small muted" style={{ marginLeft: 12 }}>Status</span>
          {WORK_STATUSES.map((s) => <button key={s} className={`badge ${statuses.includes(s) ? "accent" : "outline"}`} style={{ cursor: "pointer" }} aria-pressed={statuses.includes(s)} onClick={() => setStatuses((x) => toggleIn(x, s))}>{WORK_LABEL[s]}</button>)}
        </div>
      </div>

      {selected.size > 0 && (
        <div className="row" style={{ marginBottom: 10 }}>
          <b>{selected.size} selected</b>
          <button className="btn sm" onClick={() => setModal("export")}><Download size={14} /> Export…</button>
          {project?.shared_project_id && <button className="btn sm primary" onClick={() => setModal("publish")}><Share2 size={14} /> Publish…</button>}
          <button className="btn sm ghost" onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}

      {items.isLoading ? <Spinner /> : !list.length ? (
        <div className="card"><Empty title="No items match">Approve cards in the Review inbox, or add an item directly. Try clearing filters to see Done items.</Empty></div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table className="table">
            <thead><tr>
              <th style={{ width: 36 }}><input type="checkbox" checked={allSelected} onChange={(e) => setSelected(e.target.checked ? new Set(list.map((i) => i.id)) : new Set())} aria-label="Select all" /></th>
              <th>Item</th><th style={{ width: 150 }}>Status</th><th style={{ width: 190 }}>Sharing</th><th style={{ width: 150 }}>Updated</th>
            </tr></thead>
            <tbody>
              {list.map((i) => (
                <tr key={i.id}>
                  <td><input type="checkbox" checked={selected.has(i.id)} onChange={() => setSelected((s) => { const n = new Set(s); if (n.has(i.id)) n.delete(i.id); else n.add(i.id); return n; })} aria-label={`Select ${i.title}`} /></td>
                  <td>
                    <button className="btn ghost" style={{ padding: 0, textAlign: "left", whiteSpace: "normal", justifyContent: "flex-start" }} onClick={() => setOpen(i.id)}>
                      <span className="row wrap" style={{ gap: 8 }}><TypeBadge type={i.type} /><span style={{ fontWeight: 600, color: "var(--text)" }}>{i.title}</span>
                        {i.capture_count > 0 && <span className="muted row small" style={{ gap: 3 }}><ImageIcon size={13} />{i.capture_count}</span>}
                        {i.origin === "shared" && <span className="badge neutral">from {i.created_by ?? "team"}</span>}</span>
                    </button>
                    {i.session_title && <div className="small muted">{i.session_title}</div>}
                  </td>
                  <td>
                    <select className="select" style={{ padding: "4px 26px 4px 8px", fontSize: 14 }} value={i.work_status} aria-label={`Status of ${i.title}`}
                      onChange={(e) => updateStatus.mutate({ id: i.id, work_status: e.target.value as WorkStatus })}>
                      {WORK_STATUSES.map((s) => <option key={s} value={s}>{WORK_LABEL[s]}</option>)}
                    </select>
                  </td>
                  <td><ShareBadge state={i.sharing_state} error={i.sync_error} /></td>
                  <td className="small muted">{fmtDate(i.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <ItemDrawer id={open} onClose={() => setOpen(null)} />}
      {modal === "publish" && <PublishModal ids={[...selected]} onClose={() => setModal(null)} />}
      {modal === "export" && <ExportModal ids={[...selected]} onClose={() => setModal(null)} />}
      {modal === "new" && <NewItemModal projectId={projectId} onClose={() => setModal(null)} />}
    </div>
  );
}
