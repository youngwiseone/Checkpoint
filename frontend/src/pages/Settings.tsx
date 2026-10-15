import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
type Integration = { app: "claude" | "codex"; name: string; installed: boolean; connected: boolean; config: string };
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Download, Save} from "lucide-react";
import { api } from "../api";
import type { HotkeyBinding, Project, Settings } from "../types";
import { Banner, START_SCRIPT, Spinner, Toggle, fmtBytes, useToast } from "../components/ui";
import { useAppState, useProjects } from "../state";
import { ReadinessList } from "./Welcome";
import { ProjectModal } from "./Home";
import { applyTheme, type Theme } from "../theme";

type Tab = "general" | "projects" | "shortcuts" | "transcription" | "assistants" | "storage";

function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api.get<Settings>("/api/settings") });
}

function usePatch() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.patch<{ settings: Settings; hotkeys?: { ok: boolean; bindings: HotkeyBinding[]; reason: string | null } }>("/api/settings", patch),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["state"] }); qc.invalidateQueries({ queryKey: ["readiness"] }); },
    onError: (e: Error) => toast("error", e.message),
  });
}

function Shortcuts({ s }: { s: Settings }) {
  const [hk, setHk] = useState(s.hotkeys);
  const patch = usePatch();
  const { data: state } = useAppState();
  const toast = useToast();
  const bindings = state?.desktop.hotkeys.bindings ?? [];
  const external = state?.desktop.platform === "linux";
  const labels: Record<string, string> = { capture: "Capture screenshot", capture_context: "Screenshot + always ask for a note", quick_note: "Text-only quick note", start_session: "Start session (press twice to end)", review: "Review overlay", voice: "Voice note (press again to save)", switch_project: "Switch or set up a project" };
  const save = () => patch.mutate({ hotkeys: hk }, {
    onSuccess: (r) => {
      if (r.hotkeys && !r.hotkeys.ok) toast("warning", `Some shortcuts couldn't be registered: ${r.hotkeys.reason}`);
      else toast("success", external ? "Saved. Bind the same keys in your desktop's shortcut settings." : "Shortcuts updated");
    },
  });
  return (
    <div className="stack-lg">
      <div className="card stack">
        <h3>Global shortcuts</h3>
        {external ? (
          <p className="text-2 small">
            On Linux your desktop owns global shortcuts. Add a custom shortcut for each command below, using the keys shown
            (COSMIC: <b>Settings → Input Devices → Keyboard → Keyboard shortcuts → Custom shortcuts</b>; GNOME and KDE have the same under Keyboard).
            A shortcut can't tell how long a key is held, so each gesture gets its own command. The keys you type here are only a reminder.
          </p>
        ) : (
          <p className="text-2 small">Registered with Windows so they work in games and other apps. Use F-keys, or combine a key with Ctrl/Alt/Shift (e.g. <code>Ctrl+Alt+S</code>).</p>
        )}
        {!state?.desktop.available && <Banner kind="warn">The desktop helper isn't running, so shortcuts aren't active. Launch with <code>{START_SCRIPT}</code>.</Banner>}
        {((external ? ["capture", "review", "voice"] : ["capture", "review"]) as ("capture" | "review" | "voice")[]).map((k) => {
          const b = bindings.find((x) => x.action === k);
          return (
            <div key={k} className="row">
              <label htmlFor={`hk-${k}`} className="label" style={{ width: 280 }}>{labels[k]}</label>
              {k === "voice" ? <span style={{ width: 180 }} /> : (
                <input id={`hk-${k}`} className="input mono" style={{ width: 180 }} value={hk[k] ?? ""} onChange={(e) => setHk({ ...hk, [k]: e.target.value })} />
              )}
              {b && !b.external && (b.registered ? <span className="badge success">Active</span> : <span className="badge danger" title={b.error ?? ""}>{b.error}</span>)}
              {b?.external && b.error && <span className="badge danger" title={b.error}>Not listening</span>}
              {b?.command && (
                <>
                  <code className="small" style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>{b.command}</code>
                  <button className="btn" onClick={() => navigator.clipboard.writeText(b.command!).then(() => toast("success", "Command copied"), (e: Error) => toast("error", e.message))}>Copy</button>
                </>
              )}
            </div>
          );
        })}
        <div><button className="btn primary" onClick={save} disabled={patch.isPending}><Save size={15} /> Apply shortcuts</button></div>
      </div>
      <div className="card stack">
        <h3>Capture behaviour</h3>
        <p className="hint">Tap capture for a screenshot, double tap to edit the last task, hold to review, or tap, release, then hold to dictate. The review key is optional; leave it empty to use one key.</p>
        <div className="field" style={{ maxWidth: 420 }}>
          <label htmlFor="region">Screenshot area</label>
          <select id="region" className="select" value={s.capture.region} onChange={(e) => patch.mutate({ capture: { region: e.target.value } })}>
            <option value="foreground_monitor">Whole display containing the active window</option>
            <option value="foreground_window">Just the active window</option>
          </select>
          <span className="hint">Only the chosen display/window is captured — never every screen. Exclusive-fullscreen games may capture black; use borderless or windowed mode.</span>
        </div>
      </div>
    </div>
  );
}

function Transcription({ s }: { s: Settings }) {
  const patch = usePatch();
  const qc = useQueryClient();
  const toast = useToast();
  type M = { models: { name: string; approx_mb: number; note: string; installed: boolean }[]; downloads: Record<string, { state: string; bytes_done: number; bytes_total: number; error: string | null }>; selected: string };
  const q = useQuery({ queryKey: ["transcription-models"], queryFn: () => api.get<M>("/api/transcription/models"), refetchInterval: (qq) => (Object.values(qq.state.data?.downloads ?? {}).some((d) => d.state === "downloading") ? 800 : 10000) });
  const dl = useMutation({ mutationFn: (name: string) => api.post(`/api/transcription/models/${name}/download`), onSuccess: () => qc.invalidateQueries({ queryKey: ["transcription-models"] }), onError: (e: Error) => toast("error", e.message) });
  const t = s.transcription;
  return (
    <div className="stack-lg">
      <div className="card stack">
        <h3>Speech-to-text model</h3>
        <p className="text-2 small">Runs locally with Whistle on the CPU. Downloading needs internet once; after that transcription works offline. Audio is always saved even when no model is installed.</p>
        {!q.data ? <Spinner /> : q.data.models.map((m) => {
          const d = q.data!.downloads[m.name];
          return (
            <div key={m.name} className="row" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
              <input type="radio" name="wm" checked={t.model === m.name} onChange={() => patch.mutate({ transcription: { model: m.name } })} aria-label={`Use ${m.name}`} style={{ accentColor: "var(--accent)" }} />
              <div className="grow"><b className="mono">{m.name}</b> <span className="muted small">~{m.approx_mb} MB · {m.note}</span>
                {d?.state === "downloading" && <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${Math.min(100, (d.bytes_done / Math.max(1, d.bytes_total)) * 100)}%` }} /></div>}
                {d?.state === "failed" && <p className="small" style={{ color: "var(--danger)" }}>{d.error}</p>}
              </div>
              {m.installed ? <span className="badge success">Installed</span> : d?.state === "downloading" ? <span className="small muted">{fmtBytes(d.bytes_done)} / {fmtBytes(d.bytes_total)}</span>
                : <button className="btn sm" onClick={() => dl.mutate(m.name)}><Download size={14} /> Download</button>}
            </div>
          );
        })}
      </div>
      <div className="card stack">
        <h3>Performance</h3>
        <div className="grid-2">
          <div className="field"><label htmlFor="dm">Default timing</label>
            <select id="dm" className="select" value={t.default_mode} onChange={(e) => patch.mutate({ transcription: { default_mode: e.target.value } })}>
              <option value="after">After session (recommended)</option><option value="live">Live</option>
            </select></div>
        </div>

        <Toggle checked={!t.paused} onChange={(v) => patch.mutate({ transcription: { paused: !v } })} label="Background transcription" hint="Pause to free up the CPU; recording continues and the backlog resumes later." />
        <p className="hint">Transcription and AI organisation run one at a time so they don't compete for memory.</p>
      </div>
    </div>
  );
}

function Storage({ s }: { s: Settings }) {
  const q = useQuery({ queryKey: ["storage"], queryFn: () => api.get<Record<string, number | string>>("/api/storage") });
  const [dir, setDir] = useState("");
  const toast = useToast();
  const move = useMutation({ mutationFn: () => api.post<{ message: string }>("/api/settings/data-dir", { path: dir }), onSuccess: (r) => toast("info", r.message), onError: (e: Error) => toast("error", e.message) });
  const rows: [string, string][] = [["screenshots_bytes", "Screenshots"], ["audio_bytes", "Raw audio"], ["database_bytes", "Database"], ["models_bytes", "Transcription models"], ["shared_cache_bytes", "Shared attachment cache"]];
  return (
    <div className="stack-lg">
      <div className="card stack">
        <h3>Storage</h3>
        <p className="small text-2">Data folder: <code>{s.data_dir}</code></p>
        {!q.data ? <Spinner /> : (
          <table className="table"><tbody>{rows.map(([k, l]) => <tr key={k}><td>{l}</td><td className="mono">{fmtBytes(Number(q.data![k] ?? 0))}</td></tr>)}</tbody></table>
        )}
        <p className="hint">Delete raw audio or whole sessions from each session's page. Approved items are always kept.</p>
      </div>
      <div className="card stack">
        <h3>Move data folder</h3>
        <div className="row"><input className="input" placeholder="D:\\Checkpoint" value={dir} onChange={(e) => setDir(e.target.value)} aria-label="New data folder" />
          <button className="btn" disabled={!dir.trim()} onClick={() => move.mutate()}>Set location</button></div>
        <p className="hint">Takes effect after restarting. Existing data isn't moved automatically — copy the folder first if you want to keep it.</p>
      </div>
    </div>
  );
}

function Assistants() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<Integration[]>("/api/integrations") });
  const set = useMutation({
    mutationFn: ({ app, connect }: { app: string; connect: boolean }) => api.post<Integration>(`/api/integrations/${app}/${connect ? "connect" : "disconnect"}`),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
      toast("success", r.connected ? `Connected. Fully quit and reopen ${r.name} to finish (tray icon > Quit).` : `Disconnected from ${r.name}. Restart it to apply.`);
    },
    onError: (e: Error) => toast("error", e.message),
  });
  return (
    <div className="stack-lg">
      <div className="card stack">
        <h3>AI assistants</h3>
        <p className="text-2 small">
          Connect Claude Desktop or Codex once, then ask in your chat: “Show my latest Checkpoint captures” or “List my open Checkpoint tasks”.
          Your assistant fetches the notes, linked transcript excerpts and screenshots directly. No send or approval step.
          The connector is read-only; mark tasks done in Checkpoint after testing them.
        </p>
        {!q.data ? <Spinner /> : q.data.map((i) => (
          <div key={i.app} className="row" style={{ padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
            <Bot size={18} className="muted" />
            <div className="grow">
              <b>{i.name}</b>
              <div className="small muted truncate" title={i.config}>{i.installed ? i.config : "Not found on this PC"}</div>
            </div>
            {i.connected ? <span className="badge success">Connected</span> : <span className="badge neutral">Not connected</span>}
            {i.connected
              ? <button className="btn sm" onClick={() => set.mutate({ app: i.app, connect: false })} disabled={set.isPending}>Disconnect</button>
              : <button className="btn sm primary" onClick={() => set.mutate({ app: i.app, connect: true })} disabled={set.isPending || !i.installed}>Connect</button>}
          </div>
        ))}
        <p className="hint">Connecting adds a “checkpoint” entry to that app's MCP settings and keeps a backup of the file next to it. Restart the app afterwards.</p>
      </div>

    </div>
  );
}

function Projects() {
  const { data: projects } = useProjects();
  const [editing, setEditing] = useState<Project | null>(null);
  return <div className="card stack"><h3>Projects</h3><p className="hint">A name groups your captures. No agent or repository setup needed.</p>
    {(projects ?? []).filter(p => !p.archived && !p.is_demo).map(p => <div className="row" key={p.id}><b className="grow">{p.name}</b><button className="btn sm" onClick={() => setEditing(p)}>Edit</button></div>)}
    {editing && <ProjectModal project={editing} onClose={() => setEditing(null)} />}</div>;
}

function Appearance({ s }: { s: Settings }) {
  const patch = usePatch();
  const current: Theme = s.appearance?.theme === "light" ? "light" : "dark";
  const [picked, setPicked] = useState<Theme>(current);
  useEffect(() => setPicked(current), [current]);
  const choose = (theme: Theme) => {
    if (theme === picked) return;
    setPicked(theme);
    applyTheme(theme); // instant; the settings refetch confirms it
    patch.mutate({ appearance: { theme } }, { onError: () => { setPicked(current); applyTheme(current); } });
  };
  const options: [Theme, string][] = [["dark", "Night blue"], ["light", "Light"]];
  return (
    <div className="card stack">
      <div>
        <h3>Appearance</h3>
        <p className="hint" style={{ marginTop: 2 }}>Also used by the note window and review panel.</p>
      </div>
      <div className="theme-picker" role="radiogroup" aria-label="Theme">
        {options.map(([k, label]) => (
          <button key={k} type="button" role="radio" aria-checked={picked === k} className={`theme-swatch ${picked === k ? "on" : ""}`} onClick={() => choose(k)}>
            <span className={`swatch-preview ${k}`} aria-hidden>
              <span className="sp-side" />
              <span className="sp-main"><span className="sp-line" /><span className="sp-line short" /><span className="sp-btn" /></span>
            </span>
            <span className="row" style={{ gap: 8 }}>
              <span className="swatch-radio" aria-hidden />
              <span className="label">{label}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = (params.get("tab") as Tab) || "general";
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });
  const s = useSettings();
  useEffect(() => { document.title = "Settings · Checkpoint"; return () => { document.title = "Checkpoint"; }; }, []);
  const tabs: [Tab, string][] = [["general", "General"], ["projects", "Projects"], ["shortcuts", "Capture key"], ["transcription", "Transcription"], ["assistants", "Chat connection"], ["storage", "Storage"]];
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Settings</h1><p>Optional features have clear setup steps; none of them are required to capture.</p></div></div>
      <div className="tabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>)}</div>
      {!s.data ? <Spinner /> : (
        <>
          {tab === "general" && <Appearance s={s.data} />}
          {tab === "general" && <div className="card" style={{ marginTop: 16 }}><div className="card-head"><h3>Readiness</h3></div><ReadinessList /></div>}
          {tab === "projects" && <Projects />}
          {tab === "shortcuts" && <Shortcuts s={s.data} />}
          {tab === "transcription" && <Transcription s={s.data} />}
          {tab === "assistants" && <Assistants />}
          {tab === "storage" && <Storage s={s.data} />}
        </>
      )}
    </div>
  );
}
