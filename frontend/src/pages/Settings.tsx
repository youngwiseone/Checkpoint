import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Integration } from "../components/SendToAI";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ChevronDown, ChevronRight, Download, KeyRound, Link2, Unlink, Save, ShieldCheck } from "lucide-react";
import { api } from "../api";
import type { HotkeyBinding, Project, Settings } from "../types";
import { Banner, Spinner, Toggle, fmtBytes, useToast } from "../components/ui";
import { useAppState, useProjects } from "../state";
import { ReadinessList } from "./Welcome";
import { AgentPaths, ProjectSetupForm } from "../components/ProjectSetup";

type Tab = "general" | "projects" | "shortcuts" | "transcription" | "ai" | "assistants" | "sharing" | "storage";

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
  const labels: Record<string, string> = { capture: "Capture screenshot", capture_context: "Screenshot + always ask for a note", quick_note: "Text-only quick note", start_session: "Start session (press twice to end)", review: "Review overlay", switch_project: "Switch or set up a project" };
  const save = () => patch.mutate({ hotkeys: hk }, {
    onSuccess: (r) => {
      if (r.hotkeys && !r.hotkeys.ok) toast("warning", `Some shortcuts couldn't be registered: ${r.hotkeys.reason}`);
      else toast("success", "Shortcuts updated");
    },
  });
  return (
    <div className="stack-lg">
      <div className="card stack">
        <h3>Global shortcuts</h3>
        <p className="text-2 small">Registered with Windows so they work in games and other apps. Use F-keys, or combine a key with Ctrl/Alt/Shift (e.g. <code>Ctrl+Alt+S</code>).</p>
        {!state?.desktop.available && <Banner kind="warn">The desktop helper isn't running, so shortcuts aren't active. Launch with <code>start.cmd</code>.</Banner>}
        {(["capture", "capture_context", "quick_note", "start_session", "review", "switch_project"] as const).map((k) => {
          const b = bindings.find((x) => x.action === k);
          return (
            <div key={k} className="row">
              <label htmlFor={`hk-${k}`} className="label" style={{ width: 280 }}>{labels[k]}</label>
              <input id={`hk-${k}`} className="input mono" style={{ width: 180 }} value={hk[k] ?? ""} onChange={(e) => setHk({ ...hk, [k]: e.target.value })} />
              {b && (b.registered ? <span className="badge success">Active</span> : <span className="badge danger" title={b.error ?? ""}>{b.error}</span>)}
            </div>
          );
        })}
        <div><button className="btn primary" onClick={save} disabled={patch.isPending}><Save size={15} /> Apply shortcuts</button></div>
      </div>
      <div className="card stack">
        <h3>Capture behaviour</h3>
        <Toggle checked={s.capture.always_ask_context} onChange={(v) => patch.mutate({ capture: { always_ask_context: v } })} label="Always ask for context on F8" hint="Even when audio is recording." />
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
  const { data: appState } = useAppState();
  const tw = appState?.workers.transcription;
  const qc = useQueryClient();
  const toast = useToast();
  type M = { models: { name: string; approx_mb: number; note: string; installed: boolean }[]; downloads: Record<string, { state: string; bytes_done: number; bytes_total: number; error: string | null }>; selected: string };
  const q = useQuery({ queryKey: ["whisper-models"], queryFn: () => api.get<M>("/api/transcription/models"), refetchInterval: (qq) => (Object.values(qq.state.data?.downloads ?? {}).some((d) => d.state === "downloading") ? 800 : 10000) });
  const dl = useMutation({ mutationFn: (name: string) => api.post(`/api/transcription/models/${name}/download`), onSuccess: () => qc.invalidateQueries({ queryKey: ["whisper-models"] }), onError: (e: Error) => toast("error", e.message) });
  const t = s.transcription;
  return (
    <div className="stack-lg">
      <div className="card stack">
        <h3>Speech-to-text model</h3>
        <p className="text-2 small">Runs locally with faster-whisper. Downloading needs internet once; after that transcription works offline. Audio is always saved even when no model is installed.</p>
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
          <div className="field"><label htmlFor="dev">Device</label>
            <select id="dev" className="select" value={t.device} onChange={(e) => patch.mutate({ transcription: { device: e.target.value } })}>
              <option value="cpu">CPU (int8) — works everywhere</option><option value="cuda">NVIDIA GPU (CUDA) — falls back to CPU if unavailable</option>
            </select></div>
          <div className="field"><label htmlFor="thr">CPU threads</label>
            <input id="thr" type="number" min={1} max={16} className="input" value={t.cpu_threads} onChange={(e) => patch.mutate({ transcription: { cpu_threads: Math.max(1, Math.min(16, Number(e.target.value) || 2)) } })} />
            <span className="hint">Lower = less interference with games.</span></div>
          <div className="field"><label htmlFor="dm">Default timing</label>
            <select id="dm" className="select" value={t.default_mode} onChange={(e) => patch.mutate({ transcription: { default_mode: e.target.value } })}>
              <option value="after">After session (recommended)</option><option value="live">Live</option>
            </select></div>
        </div>
        {t.device === "cuda" && tw?.gpu_fallback && <Banner kind="warn"><p>{tw.gpu_fallback}</p><p className="small">GPU transcription needs NVIDIA's CUDA 12 and cuDNN 9 runtime libraries. The CPU works fine for the base model.</p></Banner>}
        <Toggle checked={!t.paused} onChange={(v) => patch.mutate({ transcription: { paused: !v } })} label="Background transcription" hint="Pause to free up the CPU; recording continues and the backlog resumes later." />
        <p className="hint">Transcription and AI organisation run one at a time so they don't compete for memory.</p>
      </div>
    </div>
  );
}

function LocalAI({ s }: { s: Settings }) {
  const patch = usePatch();
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useState(s.ai.base_url);
  const [model, setModel] = useState(s.ai.model);
  type St = { reachable: boolean; version: string | null; models: { name: string; parameters?: string }[]; model: string; model_installed: boolean; enabled: boolean; error: string | null; pulls: Record<string, { state: string; status: string; completed: number; total: number; error: string | null }> };
  const st = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get<St>("/api/ai/status"), refetchInterval: (q) => (Object.values(q.state.data?.pulls ?? {}).some((p) => p.state === "pulling") ? 1000 : 10000) });
  const pull = useMutation({ mutationFn: (m: string) => api.post("/api/ai/pull", { model: m }), onSuccess: () => qc.invalidateQueries({ queryKey: ["ai-status"] }), onError: (e: Error) => toast("error", e.message) });
  const d = st.data;
  const p = d?.pulls[model];
  return (
    <div className="stack-lg">
      <div className="card stack">
        <div className="row"><h3 className="grow">Local AI organisation</h3>
          <Toggle checked={s.ai.enabled} onChange={(v) => patch.mutate({ ai: { enabled: v } })} label={s.ai.enabled ? "On" : "Off"} disabled={!s.ai.enabled && !(d?.reachable && d.model_installed)} /></div>
        <p className="text-2 small">
          Turns transcripts and notes into suggested cards after a session, using Ollama on this PC. It reads text only (not screenshots), never calls a cloud service,
          and every suggestion links back to the exact words it came from. It can still misread a conversation — you approve everything.
        </p>
        {!st.data ? <Spinner label="Checking Ollama…" /> : !d!.reachable ? (
          <Banner kind="warn"><p><b>Ollama isn't available.</b> Checkpoint starts it automatically when it's installed; if this persists, install it from ollama.com (free) and come back. Everything else works without it.</p></Banner>
        ) : (
          <p className="small"><span className="dot ok" /> Ollama {d!.version} · {d!.models.length} model(s) installed</p>
        )}
        <div className="grid-2">
          <div className="field"><label htmlFor="ourl">Ollama address</label>
            <div className="row"><input id="ourl" className="input" value={url} onChange={(e) => setUrl(e.target.value)} />
              <button className="btn" onClick={() => patch.mutate({ ai: { base_url: url } }, { onSuccess: () => st.refetch() })}>Save</button></div>
            <span className="hint">Local only. The default is http://127.0.0.1:11434.</span></div>
          <div className="field"><label htmlFor="omodel">Model</label>
            <div className="row">
              <input id="omodel" className="input mono" list="installed-models" value={model} onChange={(e) => setModel(e.target.value)} />
              <datalist id="installed-models">{d?.models.map((m) => <option key={m.name} value={m.name} />)}</datalist>
              <button className="btn" onClick={() => patch.mutate({ ai: { model } }, { onSuccess: () => st.refetch() })}>Use</button>
            </div>
            <span className="hint">qwen3:4b is a modest starting point (~2.5 GB). Accuracy varies by session.</span></div>
        </div>
        {d?.reachable && (
          <div className="row">
            {d.model_installed && model === s.ai.model ? <span className="badge success">{s.ai.model} installed</span> : (
              <button className="btn sm" onClick={() => pull.mutate(model)} disabled={p?.state === "pulling"}><Download size={14} /> Download {model} with Ollama</button>
            )}
            {p?.state === "pulling" && <><span className="small muted">{p.status}</span><div className="progress grow"><div style={{ width: `${p.total ? (p.completed / p.total) * 100 : 3}%` }} /></div></>}
            {p?.state === "failed" && <span className="small" style={{ color: "var(--danger)" }}>{p.error}</span>}
            {p?.state === "done" && <span className="badge success">Downloaded</span>}
          </div>
        )}
      </div>
      <AutoCapture s={s} installed={d?.reachable ? d.models.map((m) => m.name) : null} onPull={(m) => pull.mutate(m)} pulls={d?.pulls ?? {}} />
      <details className="card">
        <summary className="label" style={{ cursor: "pointer" }}>Advanced</summary>
        <div className="grid-2" style={{ marginTop: 12 }}>
          {([["window_before_s", "Screenshot window before (s)"], ["window_after_s", "Screenshot window after (s)"], ["chunk_chars", "Text per request (characters)"], ["timeout_seconds", "Request timeout (s)"]] as const).map(([k, l]) => (
            <div key={k} className="field"><label htmlFor={k}>{l}</label>
              <input id={k} type="number" className="input" defaultValue={s.ai[k]} onBlur={(e) => patch.mutate({ ai: { [k]: Number(e.target.value) } })} /></div>
          ))}
        </div>
        <p className="hint" style={{ marginTop: 8 }}>Screenshots taken within this window around a spoken observation are suggested as possibly related — never attached as proof.</p>
      </details>
    </div>
  );
}

function AutoCapture({ s, installed, onPull, pulls }: { s: Settings; installed: string[] | null; onPull: (m: string) => void; pulls: Record<string, { state: string; status: string; error: string | null }> }) {
  const patch = usePatch();
  const { data: appState } = useAppState();
  const a = s.auto_capture;
  const [model, setModel] = useState(a.model);
  const w = appState?.workers.auto_capture;
  const has = installed?.includes(a.model) || installed?.includes(`${a.model}:latest`);
  const p = pulls[a.model];
  return (
    <div className="card stack">
      <div className="row"><h3 className="grow">Auto screenshots</h3>
        <Toggle checked={a.enabled} onChange={(v) => patch.mutate({ auto_capture: { enabled: v } })} label={a.enabled ? "On" : "Off"} /></div>
      <p className="text-2 small">
        A small local decision model (Jev-style, through Ollama) reads each live transcript line and gives the chance that you're pointing out a bug, glitch or problem on screen.
        It only decides; it never writes text. The last {a.buffer_seconds} s of your screen is kept in memory only, so the screenshot shows the moment the words were said.
        Only frames it picks are saved, as markers like F8.
      </p>
      <Banner kind="info"><p>Needs <b>live</b> transcription: choose “Live” when starting a session (or set it as the default in Transcription).</p></Banner>
      {a.enabled && w?.state === "needs_live" && <Banner kind="warn"><p>This session isn't transcribing live, so auto screenshots are paused.</p></Banner>}
      {a.enabled && w?.state === "watching" && <p className="small"><span className="dot ok" /> Watching this session · {w.count} auto screenshot(s)</p>}
      {w?.error && a.enabled && <p className="small" style={{ color: "var(--warn)" }}>{w.error}</p>}
      <div className="grid-2">
        <div className="field"><label htmlFor="acmodel">Decision model</label>
          <div className="row">
            <input id="acmodel" className="input mono" list="installed-models" value={model} onChange={(e) => setModel(e.target.value)} />
            <button className="btn" onClick={() => patch.mutate({ auto_capture: { model } })}>Use</button>
          </div>
          <span className="hint">Needs a System One model (Nimble or Tev). tev1:0.8b (~900 MB) decides in under 0.1 s once loaded.</span></div>
        <div className="field"><label htmlFor="acthr">Sensitivity</label>
          <input id="acthr" type="number" min={0.05} max={0.95} step={0.05} className="input" defaultValue={a.threshold} onBlur={(e) => patch.mutate({ auto_capture: { threshold: Math.max(0.05, Math.min(0.95, Number(e.target.value) || 0.7)) } })} />
          <span className="hint">Take a screenshot when the model is at least this sure (0–1). Ordinary chatter scores around 0.5 and clear problem reports 0.7+, so below 0.6 it fires on almost everything.</span></div>
        <div className="field"><label htmlFor="accool">Minimum gap between auto screenshots (s)</label>
          <input id="accool" type="number" min={0} className="input" defaultValue={a.cooldown_s} onBlur={(e) => patch.mutate({ auto_capture: { cooldown_s: Math.max(0, Number(e.target.value) || 0) } })} /></div>
      </div>
      {installed && (
        <div className="row">
          {has ? <span className="badge success">{a.model} installed</span> : (
            <button className="btn sm" onClick={() => onPull(a.model)} disabled={p?.state === "pulling"}><Download size={14} /> Download {a.model} with Ollama</button>
          )}
          {p?.state === "pulling" && <span className="small muted">{p.status}</span>}
          {p?.state === "failed" && <span className="small" style={{ color: "var(--danger)" }}>{p.error}</span>}
        </div>
      )}
    </div>
  );
}

function Sharing({ s }: { s: Settings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useState(s.sharing.server_url);
  const [token, setToken] = useState("");
  const [name, setName] = useState(s.sharing.display_name);
  const [insecure, setInsecure] = useState(s.sharing.allow_insecure_private_network);
  const status = useQuery({ queryKey: ["share-status"], queryFn: () => api.get<{ server_url: string; token_saved: boolean; outbox: Record<string, number>; offline_reason: string | null }>("/api/share/status") });
  const save = useMutation({
    mutationFn: () => api.post<{ workspace: { name: string } }>("/api/share/config", { server_url: url, token: token || null, display_name: name, allow_insecure_private_network: insecure }),
    onSuccess: (r) => { setToken(""); qc.invalidateQueries(); toast("success", `Connected to “${r.workspace.name}”. Token saved in Windows Credential Manager.`); },
    onError: (e: Error) => toast("error", e.message),
  });
  const forget = useMutation({ mutationFn: () => api.del("/api/share/config"), onSuccess: () => { qc.invalidateQueries(); toast("info", "Disconnected and token removed from this PC"); } });
  const connected = !!status.data?.server_url && status.data.token_saved;
  return (
    <div className="stack-lg">
      <div className="card stack">
        <h3>Shared workspace (optional)</h3>
        <p className="text-2 small">
          Publish approved items to a small server your team runs, and see each other's items. Everything is saved locally first; sharing never blocks capture.
          Display names are just labels for attribution — they aren't verified accounts. Anyone with the workspace token can read and edit shared items.
        </p>
        <div className="grid-2">
          <div className="field"><label htmlFor="surl">Server address</label><input id="surl" className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://capture.example.com" /></div>
          <div className="field"><label htmlFor="sname">Your display name</label><input id="sname" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Alex" maxLength={100} /></div>
          <div className="field"><label htmlFor="stok">Workspace token</label>
            <input id="stok" className="input" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={status.data?.token_saved ? "Saved — leave blank to keep" : "Paste the token from your server admin"} />
            <span className="hint">Stored in Windows Credential Manager, never in the browser.</span></div>
        </div>
        <Toggle checked={insecure} onChange={setInsecure} label="Trusted private network (allow plain HTTP)" hint="Only for a server on your LAN/VPN. Use HTTPS otherwise." />
        <div className="row">
          <button className="btn primary" onClick={() => save.mutate()} disabled={!url || save.isPending}><ShieldCheck size={15} /> {save.isPending ? "Testing…" : "Test & save"}</button>
          {connected && <button className="btn" onClick={() => forget.mutate()}><KeyRound size={15} /> Disconnect</button>}
          {connected && <span className="badge success">Connected</span>}
          {status.data?.offline_reason && <span className="small" style={{ color: "var(--warn)" }}>{status.data.offline_reason}</span>}
        </div>
      </div>
      {connected && <ProjectLinks />}
    </div>
  );
}

function ProjectLinks() {
  const { data: projects } = useProjects();
  const remote = useQuery({ queryKey: ["remote-projects"], queryFn: () => api.get<{ id: string; name: string }[]>("/api/share/projects") });
  const qc = useQueryClient();
  const toast = useToast();
  const link = useMutation({
    mutationFn: ({ pid, shared }: { pid: string; shared: string | null }) => api.post<Project>(`/api/projects/${pid}/link`, shared ? { shared_project_id: shared } : {}),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["projects"] }); qc.invalidateQueries({ queryKey: ["remote-projects"] }); toast("success", "Project linked. Only items you explicitly publish are uploaded."); },
    onError: (e: Error) => toast("error", e.message),
  });
  const unlink = useMutation({ mutationFn: (pid: string) => api.post(`/api/projects/${pid}/unlink`), onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }) });
  const [choice, setChoice] = useState<Record<string, string>>({});
  return (
    <div className="card stack">
      <h3>Linked projects</h3>
      <p className="text-2 small">Projects are local-only unless linked here. Nothing from a local-only project is ever uploaded.</p>
      {remote.error && <Banner kind="error">{(remote.error as Error).message}</Banner>}
      {projects?.filter((p) => !p.is_demo).map((p) => (
        <div key={p.id} className="row" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
          <b className="grow">{p.name}</b>
          {p.shared_project_id ? (
            <><span className="badge success">Shared as “{p.shared_project_name}”</span><button className="btn sm" onClick={() => unlink.mutate(p.id)}><Unlink size={14} /> Unlink</button></>
          ) : (
            <>
              <select className="select" style={{ width: 260 }} value={choice[p.id] ?? ""} onChange={(e) => setChoice({ ...choice, [p.id]: e.target.value })} aria-label={`Shared project for ${p.name}`}>
                <option value="">Create new shared project “{p.name}”</option>
                {remote.data?.map((r) => <option key={r.id} value={r.id}>Join existing: {r.name}</option>)}
              </select>
              <button className="btn sm" onClick={() => link.mutate({ pid: p.id, shared: choice[p.id] || null })}><Link2 size={14} /> Link</button>
            </>
          )}
        </div>
      ))}
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
  const recent = useQuery({ queryKey: ["handoffs"], queryFn: () => api.get<{ code: string; title: string; created_at: string; items: number }[]>("/api/handoffs") });
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
          Connect Claude Desktop or Codex once. Then tick items in <b>Items</b> and press <b>Send to Claude / Codex</b>: it copies a short prompt, and the assistant
          fetches the items, notes and screenshots from Checkpoint on this PC. The connector is read-only, makes no network requests and
          never exposes audio or full transcripts. You can also ask the assistant things like “list my open Checkpoint bugs”.
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
      {!!recent.data?.length && (
        <div className="card stack">
          <h3>Recent handoffs</h3>
          {recent.data.map((h) => <div key={h.code} className="row small"><b className="mono">{h.code}</b><span className="grow">{h.title}</span><span className="muted">{new Date(h.created_at).toLocaleString()}</span></div>)}
        </div>
      )}
    </div>
  );
}

function Projects({ s, focus }: { s: Settings; focus: string | null }) {
  const { data: projects } = useProjects();
  const [open, setOpen] = useState<Set<string>>(() => new Set(focus ? [focus] : []));
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || scrolled.current === focus) return;
    setOpen((o) => new Set(o).add(focus));
    const el = document.getElementById(`proj-${focus}`);
    if (el) { el.scrollIntoView({ block: "start" }); scrolled.current = focus; }
  }, [focus, projects]);
  const list = (projects ?? []).filter((p) => !p.is_demo && !p.archived);
  const toggle = (id: string) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <div className="stack-lg">
      <div className="card stack">
        <h3>Projects</h3>
        <p className="text-2 small">Link a project to its Git repo and a coding agent. Approved tasks then go to the agent, which works on a separate branch for each session.</p>
        {!projects ? <Spinner /> : !list.length ? <p className="muted small">No projects yet.</p> : list.map((p) => {
          const isOpen = open.has(p.id);
          return (
            <div key={p.id} id={`proj-${p.id}`} style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
              <button className="btn ghost" style={{ width: "100%", justifyContent: "flex-start", padding: "6px 4px" }} aria-expanded={isOpen} onClick={() => toggle(p.id)}>
                {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                <b className="grow truncate" style={{ textAlign: "left", color: "var(--text)" }}>{p.name}</b>
                {p.configured ? <span className="badge success">Ready to send</span> : p.repo_path ? <span className="badge warn">No agent</span> : <span className="badge neutral">Not set up</span>}
              </button>
              {isOpen && <div style={{ padding: "8px 4px 16px 26px" }}><ProjectSetupForm project={p} /></div>}
            </div>
          );
        })}
      </div>
      <details className="card">
        <summary className="label" style={{ cursor: "pointer" }}>Agent programs</summary>
        <p className="hint" style={{ margin: "8px 0 12px" }}>Leave empty to find them automatically.</p>
        <AgentPaths settings={s} />
      </details>
    </div>
  );
}

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = (params.get("tab") as Tab) || "general";
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });
  const s = useSettings();
  useEffect(() => { document.title = "Settings · Checkpoint"; return () => { document.title = "Checkpoint"; }; }, []);
  const tabs: [Tab, string][] = [["general", "General"], ["projects", "Projects"], ["shortcuts", "Shortcuts & capture"], ["transcription", "Transcription"], ["ai", "Local AI"], ["assistants", "AI assistants"], ["sharing", "Sharing"], ["storage", "Storage"]];
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Settings</h1><p>Optional features have clear setup steps; none of them are required to capture.</p></div></div>
      <div className="tabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>)}</div>
      {!s.data ? <Spinner /> : (
        <>
          {tab === "general" && <div className="card"><div className="card-head"><h3>Readiness</h3></div><ReadinessList /></div>}
          {tab === "projects" && <Projects s={s.data} focus={params.get("project")} />}
          {tab === "shortcuts" && <Shortcuts s={s.data} />}
          {tab === "transcription" && <Transcription s={s.data} />}
          {tab === "ai" && <LocalAI s={s.data} />}
          {tab === "assistants" && <Assistants />}
          {tab === "sharing" && <Sharing s={s.data} />}
          {tab === "storage" && <Storage s={s.data} />}
        </>
      )}
    </div>
  );
}
