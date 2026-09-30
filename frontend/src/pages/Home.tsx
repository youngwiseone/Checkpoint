import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Mic, MonitorSpeaker, Play, Trash2, FlaskConical, ImageOff, AppWindow, X, SlidersHorizontal } from "lucide-react";
import { api, qs } from "../api";
import type { Capture, Project, SessionSetup, SessionSummary, Settings, WatchRule } from "../types";
import { Banner, Empty, Modal, Spinner, fmtDate, fmtDuration, fmtOffset, useToast } from "../components/ui";
import { useAppState, useCurrentProject, useSettings } from "../state";
import { CaptureNoteModal } from "./SessionDetail";
import ActiveSession from "./ActiveSession";
import RecordingChips, { type ChipState } from "../components/RecordingChips";

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
          <input id="pname" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Cannon Crew playtests" maxLength={200} autoFocus />
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
    <Link to={s.state === "active" || s.state === "paused" ? "/" : `/sessions/${s.id}`} className="list-row clickable" style={{ color: "inherit", textDecoration: "none" }}>
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

function AutoStartRule({ project, settings }: { project: Project; settings: Settings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [picking, setPicking] = useState(false);
  const rules = settings.app_watch.rules;
  const mine = rules.find((r) => r.project_id === project.id);
  const apps = useQuery({ queryKey: ["running-apps"], queryFn: () => api.get<{ exe: string; title: string }[]>("/api/running-apps"), enabled: picking });
  const save = useMutation({
    mutationFn: (rule: WatchRule | null) => api.patch("/api/settings", { app_watch: { enabled: true, rules: [...rules.filter((r) => r.project_id !== project.id), ...(rule ? [rule] : [])] } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); setPicking(false); },
    onError: (e: Error) => toast("error", e.message),
  });
  const hk = settings.hotkeys.start_session;
  if (picking) {
    return (
      <div className="stack" style={{ gap: 8 }}>
        <span className="small text-2">Start the program you test, then pick it. Picking by program is safest; pick by window title if the program is shared (e.g. a game engine editor).</span>
        {apps.isLoading ? <Spinner /> : (
          <div style={{ maxHeight: 240, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
            {(apps.data ?? []).map((a) => (
              <div key={a.exe + a.title} className="row" style={{ padding: "6px 10px", borderBottom: "1px solid var(--border)" }}>
                <span className="grow truncate small" title={a.title}><b>{a.exe}</b> — {a.title}</span>
                <button className="btn sm" onClick={() => save.mutate({ project_id: project.id, kind: "exe", match: a.exe })}>This program</button>
                <button className="btn sm ghost" onClick={() => save.mutate({ project_id: project.id, kind: "title", match: a.title })}>This window title</button>
              </div>
            ))}
          </div>
        )}
        <div className="row"><button className="btn sm" onClick={() => apps.refetch()}>Refresh list</button><button className="btn sm ghost" onClick={() => setPicking(false)}>Cancel</button></div>
      </div>
    );
  }
  return mine ? (
    <div className="row small text-2 wrap">
      <AppWindow size={15} />
      <span>When <b>{mine.match}</b> {mine.kind === "title" ? "(window title) " : ""}is running, Checkpoint offers to start a session — press <kbd>{hk}</kbd>.</span>
      <button className="btn sm ghost" onClick={() => setPicking(true)}>Change</button>
      <button className="icon-btn" aria-label="Stop watching" title="Stop watching" onClick={() => save.mutate(null)}><X size={14} /></button>
    </div>
  ) : (
    <div className="row small text-2 wrap">
      <AppWindow size={15} />
      <span>Press <kbd>{hk}</kbd> anywhere to start with these settings.</span>
      <button className="btn sm ghost" onClick={() => setPicking(true)}>Offer a session when a program starts…</button>
    </div>
  );
}

function QuickStart({ project, settings }: { project: Project; settings: Settings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const setupQ = useQuery({ queryKey: ["setup", project.id], queryFn: () => api.get<SessionSetup>(`/api/projects/${project.id}/setup`) });
  const [setup, setSetup] = useState<SessionSetup | null>(null);
  useEffect(() => { if (setupQ.data) setSetup(setupQ.data); }, [setupQ.data]);
  const aiAvailable = settings.ai.enabled;
  const start = useMutation({
    mutationFn: () => api.post<{ session_id: string }>("/api/sessions", { ...setup!, project_id: project.id, transcription_mode: setup!.mic.enabled || setup!.loopback.enabled ? setup!.transcription_mode : "off" }),
    onSuccess: () => qc.invalidateQueries(),
    onError: (e: Error) => toast("error", e.message),
  });
  const auto = useMutation({
    mutationFn: (on: boolean) => api.patch("/api/settings", { auto_capture: { enabled: on } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  if (!setup) return <div className="card"><Spinner /></div>;
  const v: ChipState = {
    mic: setup.mic.enabled, micLabel: setup.mic.label, loop: setup.loopback.enabled, loopLabel: setup.loopback.label,
    live: setup.transcription_mode === "live", auto: settings.auto_capture.enabled, ai: setup.ai_enabled, ask: setup.always_ask_context,
  };
  const change = (p: Partial<ChipState>) => {
    if (p.auto !== undefined) {
      auto.mutate(p.auto);
      if (p.auto && !v.live) p = { ...p, live: true };  // auto screenshots need the live transcript
    }
    setSetup((s) => s && ({
      ...s,
      mic: p.mic !== undefined ? { ...s.mic, enabled: p.mic } : s.mic,
      loopback: p.loop !== undefined ? { ...s.loopback, enabled: p.loop } : s.loopback,
      transcription_mode: p.live !== undefined ? (p.live ? "live" : "after") : s.transcription_mode,
      ai_enabled: p.ai ?? s.ai_enabled,
      always_ask_context: p.ask ?? s.always_ask_context,
    }));
  };
  const recording = v.mic || v.loop;
  return (
    <div className="card stack">
      <div className="quick-start">
        <div className="stack" style={{ gap: 10 }}>
          <div className="row"><h3 className="grow">Ready when you are</h3>
            <button className="btn sm ghost" onClick={() => nav(`/new${qs({ project: project.id })}`)}><SlidersHorizontal size={14} /> Devices, purpose & checklist…</button></div>
          <RecordingChips v={v} onChange={change} aiAvailable={aiAvailable} />
          {v.auto && !(recording && v.live) && <span className="small" style={{ color: "var(--warn)" }}>Auto screenshots need recorded audio with a live transcript.</span>}
        </div>
        <button className="btn primary lg" onClick={() => start.mutate()} disabled={start.isPending}><Play size={17} /> Start session</button>
      </div>
      {recording && <p className="hint">Recording {[v.mic && (v.micLabel || "Me"), v.loop && (v.loopLabel || "Computer audio")].filter(Boolean).join(" and ")}. Tell everyone on the call. You can change any of this while the session runs.</p>}
      <hr className="divider" style={{ margin: "2px 0" }} />
      <AutoStartRule project={project} settings={settings} />
    </div>
  );
}

export default function Home() {
  const { data: state } = useAppState();
  const { data: settings } = useSettings();
  const [project, setProject] = useCurrentProject();
  const [creating, setCreating] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const sessions = useQuery({
    queryKey: ["sessions", project?.id],
    queryFn: () => api.get<SessionSummary[]>(`/api/sessions${qs({ project_id: project!.id })}`),
    enabled: !!project,
    refetchInterval: 4000,
  });
  const interrupted = (sessions.data ?? []).filter((s) => s.state === "interrupted");
  const demo = useMutation({
    mutationFn: (on: boolean) => (on ? api.post<{ project_id: string }>("/api/demo") : api.del("/api/demo")),
    onSuccess: (r) => {
      qc.invalidateQueries();
      const pid = (r as { project_id?: string } | undefined)?.project_id;
      if (pid) setProject(pid);
    },
    onError: (e: Error) => toast("error", e.message),
  });
  const finish = useMutation({
    mutationFn: (id: string) => api.post(`/api/sessions/${id}/finish`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
    onError: (e: Error) => toast("error", e.message),
  });
  const quick = useMutation({
    mutationFn: (pid: string) => api.post("/api/sessions/quick", { project_id: pid }),
    onSuccess: () => qc.invalidateQueries(),
    onError: (e: Error) => toast("error", e.message),
  });
  const dismissOffer = useMutation({ mutationFn: () => api.post("/api/offer/dismiss"), onSuccess: () => qc.invalidateQueries({ queryKey: ["state"] }) });

  if (state?.session.active) return <ActiveSession />;
  if (!state || !settings) return <div className="page"><Spinner label="Loading…" /></div>;

  const offer = state.workers.app_watch?.offer;
  const hk = settings.hotkeys.start_session;

  if (!project) {
    return (
      <div className="page">
        <div className="card">
          <Empty icon={<FolderPlus size={34} />} title="Create your first project"
            action={<div className="row" style={{ justifyContent: "center" }}>
              <button className="btn primary" onClick={() => setCreating(true)}><FolderPlus size={16} /> New project</button>
              <button className="btn" onClick={() => demo.mutate(true)}><FlaskConical size={16} /> Load demo project</button>
            </div>}>
            A project groups sessions and items — a game, an app you're testing, a report.
          </Empty>
        </div>
        {creating && <ProjectModal onClose={(p) => { setCreating(false); if (p) setProject(p.id); }} />}
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1 className="truncate">{project.name}</h1>
          <p>During a session: <kbd>{settings.hotkeys.capture}</kbd> screenshot · <kbd>{settings.hotkeys.capture_context}</kbd> screenshot + note · <kbd>{settings.hotkeys.quick_note}</kbd> text note · <kbd>{hk}</kbd> twice to end.</p>
        </div>
      </div>

      <div className="stack-lg">
        {offer && (
          <Banner kind="info" icon={<AppWindow size={18} />} action={
            <div className="row">
              <button className="btn primary sm" onClick={() => quick.mutate(offer.project_id)} disabled={quick.isPending}><Play size={14} /> Start</button>
              <button className="btn sm ghost" onClick={() => dismissOffer.mutate()}>Not now</button>
            </div>}>
            <p><b>{offer.program}</b> is running. Start a “{offer.project_name}” session? <span className="muted">(or press <kbd>{hk}</kbd>)</span></p>
          </Banner>
        )}

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

        {project.is_demo ? (
          <Banner kind="info" action={<button className="btn sm" onClick={() => demo.mutate(false)} disabled={demo.isPending}><Trash2 size={14} /> Remove demo</button>}>
            This is a demo with synthetic data — nothing here was recorded. Look through its <Link to="/items">items</Link>, then pick or create a real project in the sidebar.
          </Banner>
        ) : (
          <QuickStart project={project} settings={settings} />
        )}

        <UnfinishedInbox projectId={project.id} />

        <div className="card tight">
          <div className="row" style={{ padding: "4px 6px 6px" }}><span className="section-label grow" style={{ margin: 0 }}>Past sessions</span>
            {!project.is_demo && !state.review.pending && !(sessions.data ?? []).length && (
              <button className="btn ghost sm" onClick={() => demo.mutate(true)} disabled={demo.isPending}><FlaskConical size={14} /> Load demo project</button>
            )}</div>
          {sessions.isLoading ? <Spinner /> : !sessions.data?.length ? (
            <Empty icon={<ImageOff size={30} />} title="No sessions yet">Start one above — captures, audio and a checklist are grouped per session.</Empty>
          ) : sessions.data.map((s) => <SessionRow key={s.id} s={s} />)}
        </div>
      </div>
    </div>
  );
}
