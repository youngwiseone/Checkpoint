import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Mic, MonitorSpeaker, Pencil, Play, Plus, Share2, Trash2, FlaskConical, ImageOff } from "lucide-react";
import { api, qs } from "../api";
import type { Capture, Project, SessionSummary } from "../types";
import { Banner, Empty, Modal, Spinner, fmtDate, fmtDuration, fmtOffset, useToast } from "../components/ui";
import { useAppState, useProjects } from "../state";
import { CaptureNoteModal } from "./SessionDetail";

export function ProjectModal({ project, onClose }: { project?: Project; onClose: (p?: Project) => void }) {
  const [name, setName] = useState(project?.name ?? "");
  const [description, setDescription] = useState(project?.description ?? "");
  const [glossary, setGlossary] = useState(project?.glossary ?? "");
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: () =>
      project
        ? api.patch<Project>(`/api/projects/${project.id}`, { name, description, glossary })
        : api.post<Project>("/api/projects", { name, description, glossary }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      onClose(p);
    },
    onError: (e: Error) => toast("error", e.message),
  });
  return (
    <Modal
      title={project ? "Project details" : "New project"}
      onClose={() => onClose()}
      footer={
        <>
          <button className="btn" onClick={() => onClose()}>Cancel</button>
          <button className="btn primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>{project ? "Save" : "Create project"}</button>
        </>
      }
    >
      <form className="stack" onSubmit={(e) => { e.preventDefault(); if (name.trim()) save.mutate(); }}>
        <div className="field">
          <label htmlFor="pname">Name</label>
          <input id="pname" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Cannon Crew playtests" maxLength={200} />
        </div>
        <div className="field">
          <label htmlFor="pdesc">Short description <span className="muted">(optional)</span></label>
          <textarea id="pdesc" className="textarea" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this project? Helps the local AI understand context." />
        </div>
        <div className="field">
          <label htmlFor="pglos">Glossary <span className="muted">(optional)</span></label>
          <textarea id="pglos" className="textarea" rows={2} value={glossary} onChange={(e) => setGlossary(e.target.value)} placeholder="Product names and terms, comma-separated — e.g. Sir Spin A Lot, Land / No Land" />
          <span className="hint">Used to preserve spellings during transcription and organisation.</span>
        </div>
      </form>
    </Modal>
  );
}

function SessionRow({ s }: { s: SessionSummary }) {
  const stateBadge =
    s.state === "active" ? <span className="badge danger">Recording</span>
      : s.state === "paused" ? <span className="badge warn">Paused</span>
      : s.state === "interrupted" ? <span className="badge warn">Interrupted</span>
      : null;
  const trans = s.transcription;
  const backlog = trans.queued + trans.running;
  return (
    <Link to={s.state === "active" || s.state === "paused" ? "/session" : `/sessions/${s.id}`} className="list-row clickable" style={{ color: "inherit", textDecoration: "none" }}>
      <div className="grow">
        <div className="row"><b className="truncate">{s.title}</b>{stateBadge}{s.is_demo && <span className="badge accent">Demo</span>}</div>
        <div className="muted small row wrap" style={{ gap: 12 }}>
          <span>{fmtDate(s.started_at)} · {fmtDuration(s.started_at, s.ended_at ?? s.last_heartbeat_at)}</span>
          <span>{s.capture_count} screenshot{s.capture_count === 1 ? "" : "s"}</span>
          {s.sources.map((src) => (
            <span key={src.id} className="row" style={{ gap: 4 }}>{src.kind === "mic" ? <Mic size={13} /> : <MonitorSpeaker size={13} />}{src.label}</span>
          ))}
          {backlog > 0 && <span>{backlog} audio block{backlog === 1 ? "" : "s"} to transcribe</span>}
          {s.processing_state === "running" && <span>Organising…</span>}
        </div>
      </div>
      {s.pending_cards > 0 ? <span className="badge accent">{s.pending_cards} to review</span> : s.card_count > 0 ? <span className="badge success">Reviewed</span> : null}
    </Link>
  );
}

function UnfinishedInbox({ projectId }: { projectId: string }) {
  const q = useQuery({ queryKey: ["unfinished", projectId], queryFn: () => api.get<Capture[]>(`/api/captures/unfinished${qs({ project_id: projectId })}`) });
  const [open, setOpen] = useState<Capture | null>(null);
  if (!q.data?.length) return null;
  return (
    <div className="card">
      <div className="card-head"><h3>Unfinished captures</h3><span className="muted small">Screenshots kept for later — add context or discard</span></div>
      <div className="row wrap" style={{ gap: 12 }}>
        {q.data.map((c) => (
          <button key={c.id} className="btn ghost" style={{ padding: 6, flexDirection: "column", alignItems: "flex-start" }} onClick={() => setOpen(c)}>
            <img src={c.thumb_url} className="thumb" alt={`Unfinished screenshot ${c.window_title}`} style={{ width: 160, height: 90 }} />
            <span className="small muted">{fmtDate(c.taken_at)}{c.offset_ms != null ? ` · ${fmtOffset(c.offset_ms)}` : ""}</span>
          </button>
        ))}
      </div>
      {open && <CaptureNoteModal capture={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

export default function Home() {
  const { data: projects, isLoading } = useProjects();
  const { data: state } = useAppState();
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<Project | "new" | null>(null);
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const visible = (projects ?? []).filter((p) => !p.archived);
  useEffect(() => {
    if (!selected && visible.length) setSelected(visible[0].id);
  }, [visible, selected]);
  const project = visible.find((p) => p.id === selected);
  const sessions = useQuery({
    queryKey: ["sessions", selected],
    queryFn: () => api.get<SessionSummary[]>(`/api/sessions${qs({ project_id: selected })}`),
    enabled: !!selected,
    refetchInterval: 4000,
  });
  const interrupted = (sessions.data ?? []).filter((s) => s.state === "interrupted");
  const demo = useMutation({
    mutationFn: (on: boolean) => (on ? api.post<{ project_id: string }>("/api/demo") : api.del("/api/demo")),
    onSuccess: (r) => {
      qc.invalidateQueries();
      if (r && (r as { project_id?: string }).project_id) setSelected((r as { project_id: string }).project_id);
      else setSelected(null);
    },
    onError: (e: Error) => toast("error", e.message),
  });
  const finish = useMutation({
    mutationFn: (id: string) => api.post(`/api/sessions/${id}/finish`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
    onError: (e: Error) => toast("error", e.message),
  });
  const hasDemo = visible.some((p) => p.is_demo);

  return (
    <div className="page wide">
      <div className="page-head">
        <div className="grow">
          <h1>Projects & sessions</h1>
          <p>Pick a project, start a session, and capture with <kbd>F8</kbd>, <kbd>Shift+F8</kbd> or <kbd>F9</kbd>.</p>
        </div>
        {state?.session.active ? (
          <button className="btn primary" onClick={() => nav("/session")}><Play size={16} /> Go to active session</button>
        ) : (
          <button className="btn primary" disabled={!project || project.is_demo} onClick={() => nav(`/new${qs({ project: selected })}`)} title={project?.is_demo ? "Demo projects are read-only examples" : undefined}>
            <Plus size={16} /> New session
          </button>
        )}
      </div>

      {isLoading ? <Spinner label="Loading projects…" /> : !visible.length ? (
        <div className="card">
          <Empty icon={<FolderPlus size={34} />} title="Create your first project"
            action={<div className="row" style={{ justifyContent: "center" }}>
              <button className="btn primary" onClick={() => setEditing("new")}><FolderPlus size={16} /> New project</button>
              <button className="btn" onClick={() => demo.mutate(true)}><FlaskConical size={16} /> Load demo project</button>
            </div>}>
            A project groups sessions and items — a game, an app you're testing, a report. F8/F9 without a session also save to a project.
          </Empty>
        </div>
      ) : (
        <div className="split split-left">
          <div className="card tight">
            <div className="row" style={{ padding: "4px 4px 8px" }}>
              <span className="section-label" style={{ margin: 0, flex: 1 }}>Projects</span>
              <button className="icon-btn" aria-label="New project" title="New project" onClick={() => setEditing("new")}><Plus size={17} /></button>
            </div>
            {visible.map((p) => (
              <button key={p.id} className={`list-row clickable ${p.id === selected ? "selected" : ""}`}
                style={{ width: "100%", textAlign: "left", background: p.id === selected ? "var(--accent-soft)" : undefined, border: "none", color: "inherit", cursor: "pointer" }}
                onClick={() => setSelected(p.id)} aria-current={p.id === selected}>
                <div className="grow">
                  <div className="row"><span className="truncate" style={{ fontWeight: 600 }}>{p.name}</span>{p.is_demo && <span className="badge accent">Demo</span>}{p.shared_project_id && <Share2 size={14} className="muted" aria-label="Shared" />}</div>
                  <div className="muted small">{p.session_count} session{p.session_count === 1 ? "" : "s"} · {p.open_items} open item{p.open_items === 1 ? "" : "s"}</div>
                </div>
                {p.pending_cards > 0 && <span className="badge accent">{p.pending_cards}</span>}
              </button>
            ))}
            <hr className="divider" />
            {hasDemo ? (
              <button className="btn ghost sm" onClick={() => demo.mutate(false)} disabled={demo.isPending}><Trash2 size={14} /> Remove demo project</button>
            ) : (
              <button className="btn ghost sm" onClick={() => demo.mutate(true)} disabled={demo.isPending}><FlaskConical size={14} /> Load demo project</button>
            )}
          </div>

          {project && (
            <div className="stack-lg">
              <div className="card">
                <div className="row top">
                  <div className="grow">
                    <h2>{project.name}</h2>
                    {project.description && <p className="text-2" style={{ marginTop: 4 }}>{project.description}</p>}
                    <div className="row wrap small muted" style={{ marginTop: 8 }}>
                      {project.shared_project_id ? <span className="badge success"><Share2 size={12} /> Shared as “{project.shared_project_name}”</span> : <span className="badge neutral">Local only</span>}
                      {project.glossary && <span>Glossary: {project.glossary.slice(0, 80)}{project.glossary.length > 80 ? "…" : ""}</span>}
                    </div>
                  </div>
                  {!project.is_demo && <button className="btn sm" onClick={() => setEditing(project)}><Pencil size={14} /> Edit</button>}
                  <Link to={`/items${qs({ project: project.id })}`} className="btn sm">Items</Link>
                </div>
                {project.is_demo && (
                  <div style={{ marginTop: 12 }}>
                    <Banner kind="info">This is a demo with synthetic data. Nothing here was recorded. Remove it any time from the project list.</Banner>
                  </div>
                )}
              </div>

              {interrupted.map((s) => (
                <Banner key={s.id} kind="warn" action={
                  <div className="row">
                    <Link className="btn sm" to={`/new${qs({ project: s.project_id, resume: s.id })}`}>Resume session</Link>
                    <button className="btn sm" onClick={() => finish.mutate(s.id)}>Mark as ended</button>
                  </div>}>
                  <p><b>“{s.title}” was interrupted</b> — the app closed while it was running.</p>
                  <p className="small text-2">Screenshots, notes and audio saved up to {fmtDate(s.last_heartbeat_at)} are intact. Recording will not restart unless you resume.</p>
                </Banner>
              ))}

              <UnfinishedInbox projectId={project.id} />

              <div className="card tight">
                <div className="row" style={{ padding: "4px 6px 6px" }}><span className="section-label" style={{ margin: 0 }}>Sessions</span></div>
                {sessions.isLoading ? <Spinner /> : !sessions.data?.length ? (
                  <Empty icon={<ImageOff size={30} />} title="No sessions yet"
                    action={!project.is_demo && !state?.session.active && <button className="btn primary" onClick={() => nav(`/new${qs({ project: project.id })}`)}><Plus size={16} /> New session</button>}>
                    Start a session to group captures, audio and a checklist together.
                  </Empty>
                ) : sessions.data.map((s) => <SessionRow key={s.id} s={s} />)}
              </div>
            </div>
          )}
        </div>
      )}
      {editing && <ProjectModal project={editing === "new" ? undefined : editing} onClose={(p) => { setEditing(null); if (p) setSelected(p.id); }} />}
    </div>
  );
}
