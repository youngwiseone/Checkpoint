import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ListChecks, Radio, Settings as SettingsIcon, Lock, Plus, Pencil } from "lucide-react";
import { api } from "./api";
import { useAppState, useCurrentProject, useNoticeToasts, useProjects } from "./state";
import { Spinner } from "./components/ui";
import Home, { ProjectModal } from "./pages/Home";
import Welcome from "./pages/Welcome";
import NewSession from "./pages/NewSession";
import SessionDetail from "./pages/SessionDetail";
import Items from "./pages/Items";
import SettingsPage from "./pages/Settings";
import type { Project } from "./types";
import { useThemeSync } from "./theme";

function SignedOut() {
  return (
    <div style={{ display: "grid", placeItems: "center", height: "100%", padding: 24 }}>
      <div className="card" style={{ maxWidth: 480, textAlign: "center", padding: 32 }}>
        <Lock size={28} className="muted" />
        <h2 style={{ margin: "12px 0 8px" }}>Open Checkpoint from the tray</h2>
        <p className="text-2">
          For your privacy this page only works when opened by the app itself. Right-click the Checkpoint icon in the
          Windows notification area and choose <b>Open Checkpoint</b>, or run <code>start.cmd</code> again.
        </p>
      </div>
    </div>
  );
}

/** One project picker for the whole app; every page shows the chosen project. */
function ProjectSwitcher() {
  const { data: projects } = useProjects();
  const [project, setProject] = useCurrentProject();
  const [editing, setEditing] = useState<Project | "new" | null>(null);
  const visible = (projects ?? []).filter((p) => !p.archived);
  return (
    <div className="project-switch">
      <span className="section-label" style={{ margin: "0 0 4px 2px" }}>Project</span>
      <div className="row" style={{ gap: 4 }}>
        <select className="select" value={project?.id ?? ""} aria-label="Project"
          onChange={(e) => (e.target.value === "__new" ? setEditing("new") : setProject(e.target.value))}>
          {!visible.length && <option value="">No projects yet</option>}
          {visible.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_demo ? " (demo)" : ""}{p.pending_cards ? ` · ${p.pending_cards} to review` : ""}</option>)}
          <option value="__new">+ New project…</option>
        </select>
        {project && !project.is_demo && <button className="icon-btn" title="Project details" aria-label="Project details" onClick={() => setEditing(project)}><Pencil size={15} /></button>}
        {!project && <button className="icon-btn" title="New project" aria-label="New project" onClick={() => setEditing("new")}><Plus size={16} /></button>}
      </div>
      {editing && <ProjectModal project={editing === "new" ? undefined : editing} onClose={(p) => { setEditing(null); if (p) setProject(p.id); }} />}
    </div>
  );
}

function Redirect({ to }: { to: string }) {
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  const target = new URL(to, "http://x");
  params.forEach((v, k) => target.searchParams.set(k, v));
  return <Navigate to={target.pathname + target.search} replace />;
}

export default function App() {
  const auth = useQuery({ queryKey: ["auth"], queryFn: () => api.get<{ authenticated: boolean }>("/api/auth/status"), staleTime: 60_000 });
  const { data: state, error } = useAppState();
  useNoticeToasts(state);
  useThemeSync();

  if (auth.isLoading) return <div style={{ padding: 40 }}><Spinner label="Loading…" /></div>;
  if (auth.data && !auth.data.authenticated) return <SignedOut />;

  const s = state?.session;
  const live = s?.active;
  const liveCls = !live ? "" : s?.paused ? "paused" : s?.audio_functioning ? "" : "quiet";

  return (
    <div className="app">
      <nav className="sidebar" aria-label="Main">
        <div className="brand"><span className="brand-mark" aria-hidden /><span>Checkpoint</span></div>
        {state?.first_run_complete && <ProjectSwitcher />}
        <NavLink to="/" end className="nav-link">
          <Radio size={18} /><span className="label">Session</span>
          {live && <span className={`nav-live ${liveCls}`} aria-label={s?.paused ? "Paused" : "Running"} />}
        </NavLink>
        <NavLink to="/items" className="nav-link">
          <ListChecks size={18} /><span className="label">Items</span>
          {!!state?.review.pending && <span className="count" title="Cards to review">{state.review.pending}</span>}
        </NavLink>
        <NavLink to="/settings" className="nav-link"><SettingsIcon size={18} /><span className="label">Settings</span></NavLink>
        <div className="sidebar-foot">
          {error ? (
            <span style={{ color: "var(--danger)" }}>Not connected to the app</span>
          ) : state ? (
            <>
              {!state.desktop.available && <span style={{ color: "var(--warn)" }}>Hotkeys unavailable (no desktop helper)</span>}
              {state.desktop.available && state.desktop.hotkeys.ok === false && <span style={{ color: "var(--warn)" }}>Shortcut conflict — see Settings</span>}
              <span>v{state.version} · local-first</span>
            </>
          ) : null}
        </div>
      </nav>
      <main className="main">
        {state && !state.first_run_complete ? (
          <Welcome />
        ) : (
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/new" element={<NewSession />} />
            <Route path="/session" element={<Navigate to="/" replace />} />
            <Route path="/sessions/:id" element={<SessionDetail />} />
            <Route path="/review" element={<Redirect to="/items?tab=review" />} />
            <Route path="/items" element={<Items />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        )}
      </main>
    </div>
  );
}
