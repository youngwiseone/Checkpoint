import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppWindow, GitBranch, Save, Search, X } from "lucide-react";
import { api } from "../api";
import type { AgentInfo, Project, RepoInspect, Settings, WatchRule } from "../types";
import { Banner, Modal, Spinner, useToast } from "./ui";
import { useSettings } from "../state";

/** The program that runs a project: Checkpoint offers a session when it starts. */
export function AutoStartRule({ project, settings }: { project: Project; settings: Settings }) {
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
                <button type="button" className="btn sm" onClick={() => save.mutate({ project_id: project.id, kind: "exe", match: a.exe })}>This program</button>
                <button type="button" className="btn sm ghost" onClick={() => save.mutate({ project_id: project.id, kind: "title", match: a.title })}>This window title</button>
              </div>
            ))}
          </div>
        )}
        <div className="row"><button type="button" className="btn sm" onClick={() => apps.refetch()}>Refresh list</button><button type="button" className="btn sm ghost" onClick={() => setPicking(false)}>Cancel</button></div>
      </div>
    );
  }
  return mine ? (
    <div className="row small text-2 wrap">
      <AppWindow size={15} />
      <span>When <b>{mine.match}</b> {mine.kind === "title" ? "(window title) " : ""}is running, Checkpoint offers to start a session — press <kbd>{hk}</kbd>.</span>
      <button type="button" className="btn sm ghost" onClick={() => setPicking(true)}>Change</button>
      <button type="button" className="icon-btn" aria-label="Stop watching" title="Stop watching" onClick={() => save.mutate(null)}><X size={14} /></button>
    </div>
  ) : (
    <div className="row small text-2 wrap">
      <AppWindow size={15} />
      <span>Press <kbd>{hk}</kbd> anywhere to start with these settings.</span>
      <button type="button" className="btn sm ghost" onClick={() => setPicking(true)}>Offer a session when a program starts…</button>
    </div>
  );
}

export function useAgents() {
  return useQuery({ queryKey: ["agents"], queryFn: () => api.get<AgentInfo[]>("/api/agents"), staleTime: 30_000 });
}

type Draft = {
  repo_path: string;
  base_branch: string;
  default_agent: Project["default_agent"];
  agent_access: Project["agent_access"];
  setup_command: string;
  check_command: string;
  preview_command: string;
  preview_url: string;
};

function fromProject(p: Project): Draft {
  return {
    repo_path: p.repo_path ?? "",
    base_branch: p.base_branch ?? "",
    default_agent: p.default_agent ?? "none",
    agent_access: p.agent_access ?? "standard",
    setup_command: p.setup_command ?? "",
    check_command: p.check_command ?? "",
    preview_command: p.preview_command ?? "",
    preview_url: p.preview_url ?? "",
  };
}

/** Local form state for a project's repo + agent setup, saved with one PATCH. */
function useSetupDraft(project: Project, onSaved?: (p: Project) => void) {
  const qc = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(() => fromProject(project));
  const [saved, setSaved] = useState<Draft>(() => fromProject(project));
  const [repo, setRepo] = useState<RepoInspect | null>(null);
  const [repoError, setRepoError] = useState<string | null>(null);
  const inspect = useMutation({
    mutationFn: (path: string) => api.post<RepoInspect>("/api/repo/inspect", { path }),
    onSuccess: (r) => { setRepo(r); setRepoError(null); setDraft((d) => ({ ...d, repo_path: r.root })); },
    onError: (e: Error) => { setRepo(null); setRepoError(e.message); },
  });
  // Load branches for an already-set repo.
  useEffect(() => {
    if (project.repo_path) inspect.mutate(project.repo_path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);
  const save = useMutation({
    mutationFn: () => api.patch<Project>(`/api/projects/${project.id}`, {
      // Empty strings clear these (the backend ignores nulls in a PATCH).
      repo_path: draft.repo_path.trim(),
      base_branch: draft.base_branch,
      default_agent: draft.default_agent,
      agent_access: draft.agent_access,
      setup_command: draft.setup_command.trim(),
      check_command: draft.check_command.trim(),
      preview_command: draft.preview_command.trim(),
      preview_url: draft.preview_url.trim(),
    }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["flow"] });
      const next = p && typeof p === "object" && "id" in p ? fromProject(p) : draft;
      setDraft(next);
      setSaved(next);
      if (p?.repo_path && p.repo_path !== project.repo_path) inspect.mutate(p.repo_path);
      toast("success", "Project setup saved");
      onSaved?.(p);
    },
    onError: (e: Error) => { setRepoError(e.message); toast("error", e.message); },
  });
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  return { draft, setDraft, repo, repoError, inspect, save, dirty };
}

type DraftApi = ReturnType<typeof useSetupDraft>;

const ACCESS: { v: Project["agent_access"]; l: string; hint: string }[] = [
  { v: "standard", l: "Standard", hint: "Can edit files, run git add and commit, and run the check command." },
  { v: "full", l: "Full", hint: "No permission prompts, only inside the session's working copy." },
];

function SetupFields({ project, form, settings }: { project: Project; form: DraftApi; settings?: Settings }) {
  const { draft, setDraft, repo, repoError, inspect } = form;
  const agents = useAgents();
  const set = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }));
  const branches = repo?.branches ?? [];
  const branchOptions = draft.base_branch && !branches.includes(draft.base_branch) ? [draft.base_branch, ...branches] : branches;
  const agentOpts: { v: Project["default_agent"]; l: string }[] = [
    { v: "none", l: "None" },
    ...(agents.data ?? [{ id: "claude", name: "Claude Code", path: null }, { id: "codex", name: "Codex", path: null }] as AgentInfo[]).map((a) => ({ v: a.id, l: a.name })),
  ];
  const chosen = agents.data?.find((a) => a.id === draft.default_agent);
  const id = (k: string) => `ps-${project.id}-${k}`;
  return (
    <div className="stack">
      <div className="field">
        <label htmlFor={id("repo")}>Repository folder</label>
        <div className="row">
          <input id={id("repo")} className="input mono" value={draft.repo_path} placeholder={"C:\\Code\\my-game"}
            onChange={(e) => set({ repo_path: e.target.value })}
            onBlur={() => { if (draft.repo_path.trim() && draft.repo_path.trim() !== repo?.root) inspect.mutate(draft.repo_path.trim()); }} />
          <button type="button" className="btn" disabled={!draft.repo_path.trim() || inspect.isPending} onClick={() => inspect.mutate(draft.repo_path.trim())}>
            {inspect.isPending ? <span className="spinner" aria-hidden /> : <Search size={15} />} Check
          </button>
        </div>
        {repoError && <span className="small" style={{ color: "var(--danger)" }}>{repoError}</span>}
        {repo && (
          <span className="small text-2 row wrap" style={{ gap: 12 }}>
            <span className="row" style={{ gap: 4 }}><GitBranch size={13} /> On <code>{repo.current_branch ?? "?"}</code></span>
            {repo.default_branch && <span>Default <code>{repo.default_branch}</code></span>}
            <span className="truncate" title={repo.remote_url ?? ""}>{repo.remote_url ? <>Remote <code>{repo.remote_url}</code></> : "No remote"}</span>
          </span>
        )}
        <span className="hint">Each session gets its own branch and working copy, so your checkout isn't touched.</span>
      </div>

      <div className="grid-2">
        <div className="field">
          <label htmlFor={id("base")}>Base branch</label>
          <select id={id("base")} className="select" value={draft.base_branch} onChange={(e) => set({ base_branch: e.target.value })}>
            <option value="">Repository default{repo?.default_branch ? ` (${repo.default_branch})` : ""}</option>
            {branchOptions.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
          <span className="hint">New session branches start here.</span>
        </div>
        <div className="field">
          <span className="label">Coding agent</span>
          <div className="seg" role="radiogroup" aria-label="Coding agent">
            {agentOpts.map((o) => (
              <button key={o.v} type="button" role="radio" aria-checked={draft.default_agent === o.v} className={draft.default_agent === o.v ? "on" : ""} onClick={() => set({ default_agent: o.v })}>{o.l}</button>
            ))}
          </div>
          {chosen && !chosen.path && <span className="small" style={{ color: "var(--warn)" }}>{chosen.name} wasn't found on this PC. Install it, or set its location under Agent programs in Settings, Projects.</span>}
          {chosen?.path && <span className="hint truncate" title={chosen.path}>Found at <code>{chosen.path}</code></span>}
        </div>
      </div>

      {draft.default_agent !== "none" && (
        <div className="field">
          <span className="label">Agent access</span>
          {ACCESS.map((a) => (
            <label key={a.v} className="check" style={{ alignItems: "flex-start" }}>
              <input type="radio" name={id("access")} checked={draft.agent_access === a.v} onChange={() => set({ agent_access: a.v })} style={{ marginTop: 3 }} />
              <span><b>{a.l}</b> <span className="small text-2">{a.hint}</span></span>
            </label>
          ))}
          {draft.agent_access === "full" && <Banner kind="warn"><p className="small">With full access the agent can run any command without asking. Only use it for code you trust.</p></Banner>}
        </div>
      )}

      <div className="grid-2">
        <div className="field">
          <label htmlFor={id("setup")}>Setup command <span className="muted">(optional)</span></label>
          <input id={id("setup")} className="input mono" value={draft.setup_command} onChange={(e) => set({ setup_command: e.target.value })} placeholder="npm install" />
          <span className="hint">Runs once in each new working copy.</span>
        </div>
        <div className="field">
          <label htmlFor={id("check")}>Check command <span className="muted">(optional)</span></label>
          <input id={id("check")} className="input mono" value={draft.check_command} onChange={(e) => set({ check_command: e.target.value })} placeholder="npm test" />
          <span className="hint">Must pass before a task is Ready, e.g. <code>npm test</code> or <code>go vet ./...</code></span>
        </div>
        <div className="field">
          <label htmlFor={id("preview")}>Preview command <span className="muted">(optional)</span></label>
          <input id={id("preview")} className="input mono" value={draft.preview_command} onChange={(e) => set({ preview_command: e.target.value })} placeholder="npm run dev" />
          <span className="hint">Runs the app from the working copy, e.g. <code>npm run dev</code> or <code>gd run</code></span>
        </div>
        <div className="field">
          <label htmlFor={id("url")}>Preview address <span className="muted">(optional)</span></label>
          <input id={id("url")} className="input mono" value={draft.preview_url} onChange={(e) => set({ preview_url: e.target.value })} placeholder="http://localhost:5173" />
          <span className="hint">If set, Ready waits until this answers.</span>
        </div>
      </div>

      {settings && (
        <div className="field">
          <span className="label">Program that runs it</span>
          <AutoStartRule project={project} settings={settings} />
        </div>
      )}
    </div>
  );
}

/** Inline setup form (Settings, Projects). */
export function ProjectSetupForm({ project }: { project: Project }) {
  const { data: settings } = useSettings();
  const form = useSetupDraft(project);
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); form.save.mutate(); }}>
      <SetupFields project={project} form={form} settings={settings} />
      <div className="row">
        <button className="btn primary" disabled={!form.dirty || form.save.isPending}><Save size={15} /> Save</button>
        {form.dirty && <span className="small muted">Unsaved changes</span>}
      </div>
    </form>
  );
}

/** Setup as a follow-up step after creating a project, or from the project pencil. */
export function ProjectSetupModal({ project, onClose, isNew }: { project: Project; onClose: () => void; isNew?: boolean }) {
  const { data: settings } = useSettings();
  const form = useSetupDraft(project, () => onClose());
  return (
    <Modal wide title={isNew ? `Connect ${project.name} to a coding agent?` : "Repo & agent setup"} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>{isNew ? "Skip for now" : "Cancel"}</button>
        <button className="btn primary" disabled={form.save.isPending || (!isNew && !form.dirty)} onClick={() => form.save.mutate()}><Save size={15} /> Save</button>
      </>}>
      <div className="stack">
        {isNew && <p className="text-2 small">Optional. Link a Git repo so approved tasks can go to Claude Code or Codex. You can do this later in Settings.</p>}
        <SetupFields project={project} form={form} settings={settings} />
      </div>
    </Modal>
  );
}

/** Where Claude Code and Codex live. Empty = find automatically. */
export function AgentPaths({ settings }: { settings: Settings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const agents = useAgents();
  const [paths, setPaths] = useState(settings.agents ?? { claude_path: "", codex_path: "" });
  const save = useMutation({
    mutationFn: () => api.patch("/api/settings", { agents: paths }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["agents"] }); toast("success", "Agent programs saved"); },
    onError: (e: Error) => toast("error", e.message),
  });
  const rows: [keyof typeof paths, "claude" | "codex", string][] = [["claude_path", "claude", "Claude Code"], ["codex_path", "codex", "Codex"]];
  return (
    <div className="stack">
      {rows.map(([k, aid, name]) => {
        const found = agents.data?.find((a) => a.id === aid);
        return (
          <div key={k} className="field">
            <label htmlFor={`ap-${k}`}>{name}</label>
            <input id={`ap-${k}`} className="input mono" value={paths[k] ?? ""} onChange={(e) => setPaths({ ...paths, [k]: e.target.value })} placeholder="Find automatically" />
            <span className="hint">{!agents.data ? "Checking…" : found?.path ? <>Using <code>{found.path}</code></> : "Not found on this PC"}</span>
          </div>
        );
      })}
      <div><button className="btn" onClick={() => save.mutate()} disabled={save.isPending}><Save size={15} /> Save</button></div>
    </div>
  );
}
