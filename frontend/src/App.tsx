import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FolderKanban, Inbox, ListChecks, Radio, Settings as SettingsIcon, Lock } from "lucide-react";
import { api } from "./api";
import { useAppState, useNoticeToasts } from "./state";
import { Spinner } from "./components/ui";
import Home from "./pages/Home";
import Welcome from "./pages/Welcome";
import NewSession from "./pages/NewSession";
import ActiveSession from "./pages/ActiveSession";
import SessionDetail from "./pages/SessionDetail";
import Review from "./pages/Review";
import Items from "./pages/Items";
import SettingsPage from "./pages/Settings";

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

export default function App() {
  const auth = useQuery({ queryKey: ["auth"], queryFn: () => api.get<{ authenticated: boolean }>("/api/auth/status"), staleTime: 60_000 });
  const { data: state, error } = useAppState();
  useNoticeToasts(state);

  if (auth.isLoading) return <div style={{ padding: 40 }}><Spinner label="Loading…" /></div>;
  if (auth.data && !auth.data.authenticated) return <SignedOut />;

  const s = state?.session;
  const live = s?.active;
  const liveCls = !live ? "" : s?.paused ? "paused" : s?.audio_functioning ? "" : "quiet";

  return (
    <div className="app">
      <nav className="sidebar" aria-label="Main">
        <div className="brand"><span className="brand-mark" aria-hidden /><span>Checkpoint</span></div>
        <NavLink to="/" end className="nav-link"><FolderKanban size={18} /><span className="label">Projects & sessions</span></NavLink>
        <NavLink to="/session" className="nav-link">
          <Radio size={18} /><span className="label">Active session</span>
          {live && <span className={`nav-live ${liveCls}`} aria-label={s?.paused ? "Paused" : "Running"} />}
        </NavLink>
        <NavLink to="/review" className="nav-link">
          <Inbox size={18} /><span className="label">Review inbox</span>
          {!!state?.review.pending && <span className="count">{state.review.pending}</span>}
        </NavLink>
        <NavLink to="/items" className="nav-link"><ListChecks size={18} /><span className="label">Project items</span></NavLink>
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
            <Route path="/session" element={<ActiveSession />} />
            <Route path="/sessions/:id" element={<SessionDetail />} />
            <Route path="/review" element={<Review />} />
            <Route path="/items" element={<Items />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        )}
      </main>
    </div>
  );
}
