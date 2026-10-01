import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, FolderOpen, GitBranch, GitMerge, GitPullRequest, Lock, Pencil, RotateCw, Send, Square, Trash2, X } from "lucide-react";
import { api } from "../api";
import type { FlowRun, FlowTask, FlowTaskState, SendPlan, SessionFlow as Flow } from "../types";
import { Banner, Modal, TypeBadge, fmtDate, useToast } from "./ui";
import { useSettings } from "../state";

const PILL: Record<FlowTaskState, string> = {
  approved: "neutral", sending: "accent", sent: "accent", working: "accent", checking: "accent", ready: "success", needs_you: "warn", merged: "outline", done: "outline",
};
const STATE_LABEL: Record<FlowTaskState, string> = {
  approved: "Approved", sending: "Sending", sent: "Sent to agent", working: "Working", checking: "Checking", ready: "Ready", needs_you: "Needs you", merged: "Merged", done: "Done",
};
const BUSY_TASK: FlowTaskState[] = ["sending", "sent", "working", "checking"];
const BUSY_RUN: FlowRun["state"][] = ["queued", "starting", "running", "checking"];
const RUN_LABEL: Record<FlowRun["state"], [string, string]> = {
  queued: ["Queued", "neutral"], starting: ["Starting", "accent"], running: ["Working", "accent"], checking: ["Checking", "accent"],
  done: ["Done", "success"], failed: ["Failed", "danger"], cancelled: ["Stopped", "neutral"],
};
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** Multi-line text that collapses when long. Keeps newlines. */
function Clamp({ text, lines = 3, color }: { text: string; lines?: number; color?: string }) {
  const [open, setOpen] = useState(false);
  const all = text.trim().split("\n");
  const long = all.length > lines || text.length > 240;
  let short = all.slice(0, lines).join("\n");
  if (short.length > 240) short = short.slice(0, 240);
  return (
    <div className="pre small" style={{ color: color ?? "var(--text-2)", marginTop: 2 }}>
      {open || !long ? text.trim() : `${short}…`}
      {long && <button type="button" className="btn ghost sm" style={{ padding: "0 6px", marginLeft: 4 }} onClick={() => setOpen(!open)}>{open ? "Less" : "More"}</button>}
    </div>
  );
}

function NameEditor({ flow }: { flow: Flow }) {
  const s = flow.session;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(s.title);
  const qc = useQueryClient();
  const toast = useToast();
  const rename = useMutation({
    mutationFn: (n: string) => api.post(`/api/sessions/${s.id}/name`, { name: n }),
    onSuccess: () => { setEditing(false); qc.invalidateQueries({ queryKey: ["flow", s.id] }); qc.invalidateQueries({ queryKey: ["session", s.id] }); qc.invalidateQueries({ queryKey: ["state"] }); },
    onError: (e: Error) => toast("error", e.message),
  });
  if (s.name_locked) {
    return (
      <span className="row grow" style={{ gap: 8, minWidth: 0 }}>
        <b className="truncate">{s.title}</b>
        <span className="row small muted" style={{ gap: 4, minWidth: 0 }} title="The name is locked to the branch once tasks are sent">
          <Lock size={13} /><code className="truncate">{s.branch}</code>
        </span>
      </span>
    );
  }
  if (editing) {
    return (
      <form className="row grow" style={{ gap: 6 }} onSubmit={(e) => { e.preventDefault(); if (name.trim()) rename.mutate(name.trim()); }}>
        <input className="input" style={{ padding: "4px 9px" }} value={name} autoFocus aria-label="Session name" maxLength={120}
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setEditing(false); setName(s.title); } }} />
        <button className="icon-btn" aria-label="Save name" disabled={!name.trim() || rename.isPending}><Check size={16} /></button>
        <button type="button" className="icon-btn" aria-label="Cancel" onClick={() => { setEditing(false); setName(s.title); }}><X size={16} /></button>
      </form>
    );
  }
  return (
    <span className="row grow" style={{ gap: 4, minWidth: 0 }}>
      <b className="truncate">{s.title}</b>
      <button className="icon-btn" title="Rename (becomes the branch name when you send)" aria-label="Rename session" onClick={() => { setName(s.title); setEditing(true); }}><Pencil size={14} /></button>
    </span>
  );
}

function TaskRow({ t, onAction, busy, hasPreview }: { t: FlowTask; onAction: (path: string, ok: string, body?: unknown) => void; busy: boolean; hasPreview: boolean }) {
  const [answering, setAnswering] = useState(false);
  const [reply, setReply] = useState("");
  return (
    <div style={{ borderTop: "1px solid var(--border)", opacity: t.state === "merged" || t.state === "done" ? 0.7 : 1 }}>
    <div className="row top" style={{ padding: "7px 2px" }}>
      <span className="mono muted" style={{ width: 44, flex: "none", paddingTop: 1 }}>{t.code || "·"}</span>
      <div className="grow">
        <div className="row" style={{ gap: 8 }}>
          <span className="truncate" title={t.title}>{t.title}</span>
          <TypeBadge type={t.type} />
        </div>
        {t.note && <Clamp text={t.note} color={t.state === "needs_you" ? "var(--warn)" : undefined} />}
        {hasPreview && t.state === "ready" && !t.live && <span className="small muted">Not in the running preview yet</span>}
      </div>
      <span className={`badge ${PILL[t.state] ?? "neutral"}`} title={t.commit ? `Commit ${t.commit.slice(0, 10)}` : undefined}>
        {(BUSY_TASK.includes(t.state) || t.checking) && <span className="spinner" style={{ width: 11, height: 11 }} aria-hidden />}
        {t.label || STATE_LABEL[t.state] || t.state}
      </span>
      {t.state === "ready" && (
        <button className="btn sm" disabled={busy} onClick={() => onAction(`/api/tasks/${t.id}/works`, `${t.code} done`)} title="You tried it and it works">Works</button>
      )}
      {(t.state === "ready" || t.state === "needs_you") && (
        <button className="btn sm ghost" disabled={busy} onClick={() => onAction(`/api/tasks/${t.id}/still-broken`, "Added a follow-up card: review it with the others")} title="Make a follow-up card for what's still wrong">Still broken</button>
      )}
      {t.state === "needs_you" && (
        <button className="btn sm" disabled={busy} onClick={() => setAnswering((a) => !a)} title="Answer the agent and send the task back">Answer</button>
      )}
      {t.state === "needs_you" && (
        <button className="btn sm ghost" disabled={busy} onClick={() => onAction(`/api/tasks/${t.id}/resend`, "Moved back to approved")} title="Send this task to the agent again">
          <RotateCw size={13} /> Send again
        </button>
      )}
      {t.state === "merged" && (
        <button className="btn sm" disabled={busy} onClick={() => onAction(`/api/tasks/${t.id}/unmerge`, "Now its own task")}>Keep separate</button>
      )}
    </div>
    {answering && (
      <form className="row top" style={{ padding: "0 2px 8px 46px", gap: 8 }} onSubmit={(e) => {
        e.preventDefault();
        if (!reply.trim()) return;
        onAction(`/api/tasks/${t.id}/answer`, "Answered: send approved to send it back", { text: reply.trim() });
        setAnswering(false);
        setReply("");
      }}>
        <textarea className="textarea grow" rows={2} autoFocus value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Your answer"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) e.currentTarget.form?.requestSubmit(); }} />
        <button className="btn sm primary" disabled={!reply.trim() || busy}>Save</button>
      </form>
    )}
    </div>
  );
}

function RunRow({ r, onStop, onLog, busy }: { r: FlowRun; onStop: () => void; onLog: () => void; busy: boolean }) {
  const [label, cls] = RUN_LABEL[r.state] ?? [r.state, "neutral"];
  return (
    <div style={{ padding: "6px 2px", borderTop: "1px solid var(--border)" }}>
      <div className="row small" style={{ gap: 8 }}>
        <span className={`badge ${cls}`}>{BUSY_RUN.includes(r.state) && <span className="spinner" style={{ width: 11, height: 11 }} aria-hidden />}{label}</span>
        <span className="grow truncate text-2">{r.agent_label} · {plural(r.items, "task")}{r.stage && BUSY_RUN.includes(r.state) ? ` · ${r.stage}` : ""}</span>
        {(r.finished_at ?? r.created_at) && <span className="muted nowrap">{fmtDate(r.finished_at ?? r.created_at)}</span>}
        {(r.state === "queued" || r.state === "starting" || r.state === "running") && (
          <button className="btn sm" disabled={busy} onClick={onStop}><Square size={12} /> Stop</button>
        )}
        {r.log_path && <button className="btn sm ghost" onClick={onLog} title={r.log_path}>Log</button>}
      </div>
      {r.summary && <Clamp text={r.summary} lines={2} />}
      {r.error && <Clamp text={r.error} lines={2} color="var(--danger)" />}
    </div>
  );
}

function SendModal({ plan, onClose, onConfirm, sending, projectId }: { plan: SendPlan; onClose: () => void; onConfirm: () => void; sending: boolean; projectId: string }) {
  const n = plan.count;
  const blocked = plan.problems.length > 0 || (n === 0 && plan.merges.length === 0);
  return (
    <Modal title={n ? `Send ${plural(n, "task")} to ${plan.agent_label || "the agent"}` : "Add repeat reports as evidence"} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={blocked || sending} onClick={onConfirm}><Send size={15} /> {sending ? "Sending…" : "Send"}</button>
      </>}>
      <div className="stack">
        {plan.problems.length > 0 && (
          <Banner kind="error" action={<Link className="btn sm" to={`/settings?tab=projects&project=${projectId}`} onClick={onClose}>Open setup</Link>}>
            {plan.problems.map((p, i) => <p key={i} className="small">{p}</p>)}
          </Banner>
        )}
        <div className="small text-2 stack" style={{ gap: 4 }}>
          <div className="row wrap" style={{ gap: 6 }}><GitBranch size={14} /> Branch <code>{plan.branch}</code>{plan.branch_new && <span className="badge accent">new branch</span>}</div>
          {plan.repo && <div className="row" style={{ gap: 6, minWidth: 0 }}><FolderOpen size={14} /> <code className="truncate" title={plan.repo}>{plan.repo}</code></div>}
        </div>
        {plan.tasks.length > 0 && (
          <div>
            {plan.tasks.map((t) => (
              <div key={t.id} className="row" style={{ padding: "5px 0", borderTop: "1px solid var(--border)", gap: 8 }}>
                <TypeBadge type={t.type} />
                <span className="grow truncate" title={t.title}>{t.title}</span>
                {t.follow_up_of && <span className="small muted nowrap">follow-up to {t.follow_up_of}</span>}
              </div>
            ))}
          </div>
        )}
        {plan.merges.length > 0 && (
          <div>
            <div className="section-label" style={{ marginBottom: 4 }}>Repeat reports</div>
            {plan.merges.map((m) => (
              <div key={m.id} className="small row" style={{ padding: "3px 0", gap: 6 }}>
                <span className="truncate grow" title={m.title}>{m.title}</span>
                <span className="muted nowrap" title={m.into_title}>added to {m.into} as evidence</span>
              </div>
            ))}
          </div>
        )}
        {plan.queued_behind_current && <Banner kind="info"><p className="small">The agent is still busy with the last batch. These start when it finishes.</p></Banner>}
      </div>
    </Modal>
  );
}

export default function SessionFlow({ sessionId, style }: { sessionId: string; style?: CSSProperties }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: settings } = useSettings();
  const [plan, setPlan] = useState<SendPlan | null>(null);
  const [confirm, setConfirm] = useState<"merge" | "cleanup" | null>(null);
  const [showAllTasks, setShowAllTasks] = useState(false);
  const [showAllRuns, setShowAllRuns] = useState(false);
  const q = useQuery({
    queryKey: ["flow", sessionId],
    queryFn: () => api.get<Flow>(`/api/sessions/${sessionId}/flow`),
    retry: false,
    refetchInterval: (query) => {
      const f = query.state.data;
      if (!f) return 5000;
      const busy = f.tasks.some((t) => BUSY_TASK.includes(t.state) || t.checking)
        || f.runs.some((r) => BUSY_RUN.includes(r.state))
        || f.preview?.state === "starting";
      return busy ? 1500 : 5000;
    },
  });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["flow", sessionId] }); };
  const act = useMutation({
    mutationFn: (v: { path: string; ok?: string; body?: unknown }) => api.post<{ url?: string; merged?: boolean; message?: string }>(v.path, v.body),
    onSuccess: (r, v) => {
      refresh();
      if (v.path.endsWith("/merge")) toast(r?.merged ? "success" : "warning", r?.message || (r?.merged ? "Merged" : "Not merged"));
      else if (v.ok) toast("success", v.ok);
    },
    onError: (e: Error) => { refresh(); toast("error", e.message); },
  });
  const loadPlan = useMutation({
    mutationFn: () => api.get<SendPlan>(`/api/sessions/${sessionId}/send-plan`),
    onSuccess: (p) => setPlan(p),
    onError: (e: Error) => toast("error", e.message),
  });
  const send = useMutation({
    mutationFn: () => api.post<{ count: number; merged: number; run_id: string; branch: string; agent_label: string }>(`/api/sessions/${sessionId}/send`),
    onSuccess: (r) => {
      setPlan(null);
      refresh();
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      toast("success", `Sent ${plural(r.count, "task")} to ${r.agent_label}${r.merged ? `, ${r.merged} added to earlier tasks` : ""}`);
    },
    onError: (e: Error) => { refresh(); toast("error", e.message); },
  });

  const f = q.data;
  if (!f) return null;
  const { session: s, project: p, counts, preview } = f;
  const pending = counts.pending ?? 0;
  const approved = counts.approved ?? 0;
  const anySent = f.tasks.some((t) => !!t.code);
  if (!f.tasks.length && !pending && !f.runs.length && !s.branch) return null;

  const setupLink = `/settings?tab=projects&project=${p.id}`;
  const readyNotLive = f.tasks.some((t) => t.state === "ready" && !t.live);
  const tasks = showAllTasks ? f.tasks : f.tasks.slice(0, 8);
  const runs = f.runs; // newest first
  const shownRuns = showAllRuns ? runs : runs.slice(0, 2);
  const busy = act.isPending;
  const previewRunning = preview && (preview.state === "running" || preview.state === "starting");
  const previewDot = !preview ? "off" : preview.state === "running" ? "ok" : preview.state === "starting" ? "info" : preview.state === "failed" ? "bad" : "off";
  const previewText = !preview ? "Preview stopped" : preview.state === "running" ? "Preview running" : preview.state === "starting" ? "Preview starting…" : preview.state === "failed" ? "Preview failed" : "Preview stopped";

  return (
    <div className="card tight stack" style={{ gap: 10, ...style }}>
      <div className="row wrap" style={{ gap: 10 }}>
        <GitBranch size={17} className="muted" style={{ flex: "none" }} />
        <NameEditor key={s.title} flow={f} />
        {p.agent !== "none" && <span className="badge outline">{p.agent_label}</span>}
        {pending > 0 && <span className="small text-2 nowrap">{plural(pending, "card")} to review: press <kbd>{settings?.hotkeys.review || "Ctrl+F9"}</kbd></span>}
        {!!f.incoming && <span className="small muted nowrap" title="Screenshots become cards once the speech around them is transcribed">{f.incoming} more on the way</span>}
        <button className="btn primary sm" disabled={approved === 0 || loadPlan.isPending || send.isPending} onClick={() => loadPlan.mutate()}>
          <Send size={14} /> Send approved ({approved})
        </button>
      </div>

      {p.problems.length > 0 && !anySent && (
        <Banner kind="info" action={<Link className="btn sm" to={setupLink}>Set up</Link>}>
          <p className="small">Set up {p.name} to send tasks to an agent.</p>
        </Banner>
      )}

      {f.ready_to_refresh && (
        <Banner kind="success"><p className="small"><b>Ready to refresh.</b> The preview has the latest changes. Refresh or restart the app to try them.</p></Banner>
      )}
      {readyNotLive && p.has_preview && (
        <Banner kind="warn" action={<button className="btn sm" disabled={busy} onClick={() => act.mutate({ path: `/api/sessions/${s.id}/preview/restart`, ok: "Restarting the preview" })}><RotateCw size={13} /> Restart preview</button>}>
          <p className="small">Preview isn't running the latest changes.</p>
        </Banner>
      )}

      {f.tasks.length > 0 && (
        <div>
          {tasks.map((t) => <TaskRow key={t.id} t={t} busy={busy} hasPreview={p.has_preview} onAction={(path, ok, body) => act.mutate({ path, ok, body })} />)}
          {f.tasks.length > 8 && <button className="btn ghost sm" onClick={() => setShowAllTasks(!showAllTasks)}>{showAllTasks ? "Show fewer" : `Show all ${f.tasks.length}`}</button>}
        </div>
      )}

      {runs.length > 0 && (
        <div>
          <div className="row"><span className="section-label grow" style={{ margin: "2px 0 4px" }}>Agent runs</span>
            {runs.length > 2 && <button className="btn ghost sm" onClick={() => setShowAllRuns(!showAllRuns)}>{showAllRuns ? "Show fewer" : `All ${runs.length}`}</button>}</div>
          {shownRuns.map((r) => <RunRow key={r.id} r={r} busy={busy} onStop={() => act.mutate({ path: `/api/runs/${r.id}/stop`, ok: "Stopping" })}
            onLog={() => act.mutate({ path: `/api/runs/${r.id}/open-log`, ok: "Opened the log" })} />)}
        </div>
      )}

      {s.branch && (
        <div className="row wrap" style={{ gap: 8, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          {p.has_preview && (
            <>
              <span className="row small" style={{ gap: 6 }} title={preview?.error ?? undefined}>
                <span className={`dot ${previewDot}`} />{previewText}
                {preview?.url && preview.state === "running" && <a href={preview.url} target="_blank" rel="noreferrer" className="row" style={{ gap: 3 }}>{preview.url.replace(/^https?:\/\//, "")}<ExternalLink size={12} /></a>}
              </span>
              <button className="btn sm" disabled={busy} onClick={() => act.mutate({ path: `/api/sessions/${s.id}/preview/restart`, ok: "Restarting the preview" })}><RotateCw size={13} /> {previewRunning ? "Restart" : "Start"} preview</button>
              {previewRunning && <button className="btn sm" disabled={busy} onClick={() => act.mutate({ path: `/api/sessions/${s.id}/preview/stop`, ok: "Preview stopped" })}><Square size={12} /> Stop</button>}
              <span className="spacer" />
            </>
          )}
          {s.worktree_path && <button className="btn sm ghost" disabled={busy} title={s.worktree_path} onClick={() => act.mutate({ path: `/api/sessions/${s.id}/open-folder` })}><FolderOpen size={14} /> Open folder</button>}
          {s.pr_url
            ? <a className="btn sm ghost" href={s.pr_url} target="_blank" rel="noreferrer"><GitPullRequest size={14} /> View pull request</a>
            : <button className="btn sm ghost" disabled={busy} onClick={() => act.mutate({ path: `/api/sessions/${s.id}/pull-request`, ok: "Opened the pull request page in your browser" })}><GitPullRequest size={14} /> Open pull request</button>}
          {s.pr_url && <button className="btn sm ghost" disabled={busy} onClick={() => setConfirm("merge")}><GitMerge size={14} /> Merge…</button>}
          {s.worktree_path && <button className="btn sm ghost" disabled={busy} onClick={() => setConfirm("cleanup")}><Trash2 size={14} /> Clean up working copy</button>}
          {preview?.state === "failed" && preview.error && <div style={{ width: "100%" }}><Clamp text={preview.error} lines={2} color="var(--danger)" /></div>}
        </div>
      )}

      {plan && <SendModal plan={plan} projectId={p.id} sending={send.isPending} onClose={() => setPlan(null)} onConfirm={() => send.mutate()} />}
      {confirm === "merge" && (
        <Modal title="Merge the pull request?" onClose={() => setConfirm(null)} footer={
          <><button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
            <button className="btn primary" onClick={() => { setConfirm(null); act.mutate({ path: `/api/sessions/${s.id}/merge` }); }}><GitMerge size={15} /> Merge</button></>}>
          <p>Merges the pull request for <code>{s.branch}</code> into {p.base_branch ? <code>{p.base_branch}</code> : "the default branch"}. Make sure you've tried the changes first.</p>
        </Modal>
      )}
      {confirm === "cleanup" && (
        <Modal title="Clean up the working copy?" onClose={() => setConfirm(null)} footer={
          <><button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
            <button className="btn danger-solid" onClick={() => { setConfirm(null); act.mutate({ path: `/api/sessions/${s.id}/clean-up`, ok: "Working copy removed" }); }}>Clean up</button></>}>
          <p>Stops the preview and removes the folder <code>{s.worktree_path}</code>. The branch <code>{s.branch}</code> is kept, so nothing committed is lost.</p>
        </Modal>
      )}
    </div>
  );
}
