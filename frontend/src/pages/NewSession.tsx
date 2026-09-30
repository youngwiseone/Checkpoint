import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mic, MonitorSpeaker, Plus, X, Play, Info, Monitor } from "lucide-react";
import { api, qs } from "../api";
import type { AudioDevices, SessionSetup, Settings, WorkItem } from "../types";
import { Banner, Meter, Spinner, Toggle, TypeBadge, useToast } from "../components/ui";
import { useAppState, useProjects } from "../state";
import { ProjectModal } from "./Home";

type TestState = { running: boolean; kind?: string; level?: number; peak?: number; callbacks?: number; result?: string | null; error?: string | null };

function SourcePanel(props: {
  kind: "mic" | "loopback";
  enabled: boolean; setEnabled: (v: boolean) => void;
  device: string; setDevice: (v: string) => void;
  label: string; setLabel: (v: string) => void;
  devices: AudioDevices | undefined;
}) {
  const { kind, enabled, setEnabled, device, setDevice, label, setLabel, devices } = props;
  const list = kind === "mic" ? devices?.mic ?? [] : devices?.loopback ?? [];
  const def = kind === "mic" ? devices?.default_mic : devices?.default_loopback;
  const [test, setTest] = useState<TestState | null>(null);
  const [polling, setPolling] = useState(false);
  const toast = useToast();
  useEffect(() => {
    if (!polling) return;
    const t = window.setInterval(async () => {
      try {
        const st = await api.get<TestState>("/api/audio/test");
        setTest(st);
        if (!st.running) setPolling(false);
      } catch {
        setPolling(false);
      }
    }, 100);
    return () => window.clearInterval(t);
  }, [polling]);
  const runTest = async () => {
    try {
      setTest({ running: true });
      await api.post("/api/audio/test", { kind, device: device || null });
      setPolling(true);
    } catch (e) {
      toast("error", (e as Error).message);
      setTest(null);
    }
  };
  const Icon = kind === "mic" ? Mic : MonitorSpeaker;
  const title = kind === "mic" ? "Record microphone?" : "Record computer audio?";
  return (
    <div className="card">
      <div className="row top">
        <Icon size={20} className="muted" style={{ marginTop: 2 }} />
        <div className="grow">
          <Toggle checked={enabled} onChange={setEnabled} label={title}
            hint={kind === "mic" ? "Your voice, recorded as its own source." : "Everything played on the selected output device — calls, game audio, music."} />
        </div>
      </div>
      {enabled && (
        <div className="stack" style={{ marginTop: 14, paddingLeft: 32 }}>
          <div className="grid-2">
            <div className="field">
              <label htmlFor={`${kind}-dev`}>{kind === "mic" ? "Input device" : "Output device to record"}</label>
              <select id={`${kind}-dev`} className="select" value={device} onChange={(e) => setDevice(e.target.value)}>
                <option value="">System default{def ? ` (${def.replace(" [Loopback]", "")})` : ""}</option>
                {list.map((d) => <option key={d.name} value={d.name}>{"output_name" in d ? (d as { output_name: string }).output_name : d.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor={`${kind}-label`}>Source label</label>
              <input id={`${kind}-label`} className="input" value={label} maxLength={100} onChange={(e) => setLabel(e.target.value)} placeholder={kind === "mic" ? "Me" : "Call audio"} />
            </div>
          </div>
          <div className="row">
            <button className="btn sm" onClick={runTest} disabled={test?.running}>{test?.running ? "Testing…" : "Test for 3 seconds"}</button>
            <div className="grow"><Meter value={test?.running ? test.level ?? 0 : 0} active={!!test?.running && (test.callbacks ?? 0) > 0} /></div>
          </div>
          {test && !test.running && (test.error ? <p className="small" style={{ color: "var(--danger)" }}>{test.error}</p> : test.result && <p className="small text-2">{test.result}</p>)}
          {kind === "loopback" && (
            <p className="hint">
              <Info size={13} style={{ verticalAlign: -2 }} /> This records <b>the whole output device</b>, not one app. To capture only call audio,
              set your call app (e.g. Discord) to output to a separate device or virtual cable and pick that device here. The label is yours to choose —
              it doesn't identify who is speaking.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function NewSession() {
  const [params] = useSearchParams();
  const resumeId = params.get("resume");
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: state } = useAppState();
  const { data: projects } = useProjects();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.get<Settings>("/api/settings") });
  const devices = useQuery({ queryKey: ["devices"], queryFn: () => api.get<AudioDevices>("/api/audio/devices"), staleTime: 30_000 });
  const monitors = useQuery({ queryKey: ["monitors"], queryFn: () => api.get<{ monitors: { id: string; name: string; width: number; height: number }[]; foreground: string | null }>("/api/monitors") });
  const [projectId, setProjectId] = useState<string>(params.get("project") ?? "");
  const [newProject, setNewProject] = useState(false);
  const [title, setTitle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [checks, setChecks] = useState<string[]>([]);
  const [checkText, setCheckText] = useState("");
  const [bringIn, setBringIn] = useState<string[]>([]);
  const [mic, setMic] = useState(false);
  const [micDev, setMicDev] = useState("");
  const [micLabel, setMicLabel] = useState("Me");
  const [loop, setLoop] = useState(false);
  const [loopDev, setLoopDev] = useState("");
  const [loopLabel, setLoopLabel] = useState("Computer audio");
  const [target, setTarget] = useState("foreground");
  const [mode, setMode] = useState<"after" | "live">("after");
  const [ai, setAi] = useState(false);
  const [askAlways, setAskAlways] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const s = settings.data;
    if (s && !projectId && s.last_project_id) setProjectId(s.last_project_id);
  }, [settings.data, projectId]);
  // Start from how this project's last session was set up.
  const setup = useQuery({ queryKey: ["setup", projectId], queryFn: () => api.get<SessionSetup>(`/api/projects/${projectId}/setup`), enabled: !!projectId && !loaded });
  useEffect(() => {
    const x = setup.data;
    if (!x || loaded) return;
    setMic(x.mic.enabled);
    setMicDev(x.mic.device ?? "");
    setMicLabel(x.mic.label || "Me");
    setLoop(x.loopback.enabled);
    setLoopDev(x.loopback.device ?? "");
    setLoopLabel(x.loopback.label || "Computer audio");
    setMode(x.transcription_mode === "live" ? "live" : "after");
    setAi(x.ai_enabled);
    setAskAlways(x.always_ask_context);
    setTarget(x.capture_target || "foreground");
    setLoaded(true);
  }, [setup.data, loaded]);

  const realProjects = (projects ?? []).filter((p) => !p.is_demo && !p.archived);
  useEffect(() => {
    if (!projectId && realProjects.length) setProjectId(realProjects[0].id);
  }, [realProjects, projectId]);

  const openItems = useQuery({
    queryKey: ["items", projectId, "open"],
    queryFn: () => api.get<WorkItem[]>(`/api/items${qs({ project_id: projectId, status: ["open", "in_progress"] })}`),
    enabled: !!projectId && !resumeId,
  });

  const start = useMutation({
    mutationFn: () => {
      const srcs = { mic: { enabled: mic, device: micDev || null, label: micLabel }, loopback: { enabled: loop, device: loopDev || null, label: loopLabel } };
      if (resumeId) return api.post<{ session_id: string }>(`/api/sessions/${resumeId}/resume`, srcs);
      return api.post<{ session_id: string }>("/api/sessions", {
        project_id: projectId, title: title || null, purpose, checklist: checks, bring_in_item_ids: bringIn, ...srcs,
        capture_target: target, transcription_mode: mic || loop ? mode : "off", ai_enabled: ai, always_ask_context: askAlways,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries();
      nav("/");
    },
    onError: (e: Error) => toast("error", e.message),
  });

  const fgName = useMemo(() => monitors.data?.monitors.find((m) => m.id === monitors.data?.foreground)?.name, [monitors.data]);
  const addCheck = () => {
    const t = checkText.trim();
    if (t) setChecks((c) => [...c, t]);
    setCheckText("");
  };
  const aiAvailable = !!settings.data?.ai.enabled;

  if (state?.session.active) {
    return (
      <div className="page"><Banner kind="info" action={<button className="btn sm" onClick={() => nav("/")}>Open it</button>}>A session is already running. End it before starting another.</Banner></div>
    );
  }
  if (settings.isLoading || (!loaded && !!projectId && !resumeId)) return <div className="page"><Spinner label="Loading…" /></div>;

  const recordingSummary = [mic && `Microphone (${micLabel || "Me"})`, loop && `Computer audio (${loopLabel || "Computer audio"})`].filter(Boolean).join(" and ");

  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="page-head">
        <div className="grow">
          <h1>{resumeId ? "Resume interrupted session" : "New session"}</h1>
          <p>{resumeId ? "Choose which audio sources to record from now on. Nothing restarts without your say-so." : "Starts from how your last session was set up. Everything here is optional except the project."}</p>
        </div>
      </div>
      <div className="stack-lg">
        {!resumeId && (
          <div className="card stack">
            <div className="grid-2">
              <div className="field">
                <label htmlFor="proj">Project</label>
                <div className="row">
                  <select id="proj" className="select" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                    {!realProjects.length && <option value="">Create a project first</option>}
                    {realProjects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                  <button className="btn" onClick={() => setNewProject(true)} aria-label="New project"><Plus size={16} /></button>
                </div>
              </div>
              <div className="field">
                <label htmlFor="title">Session title <span className="muted">(optional)</span></label>
                <input id="title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={`Session ${new Date().toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`} />
              </div>
            </div>
            <div className="field">
              <label htmlFor="purpose">Purpose <span className="muted">(optional)</span></label>
              <input id="purpose" className="input" value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="e.g. Co-op playtest of the cannon level with Sam" />
            </div>
            <div className="field">
              <label htmlFor="check">Things I intend to check</label>
              {checks.map((c, i) => (
                <div key={i} className="row"><span className="grow">• {c}</span><button className="icon-btn" aria-label={`Remove ${c}`} onClick={() => setChecks((xs) => xs.filter((_, j) => j !== i))}><X size={15} /></button></div>
              ))}
              <div className="row">
                <input id="check" className="input" value={checkText} onChange={(e) => setCheckText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addCheck())} placeholder="Add a check and press Enter" />
                <button className="btn" onClick={addCheck} disabled={!checkText.trim()}>Add</button>
              </div>
            </div>
            {!!openItems.data?.length && (
              <details>
                <summary className="label" style={{ cursor: "pointer" }}>Bring in open project items ({openItems.data.length})</summary>
                <div style={{ marginTop: 8, maxHeight: 220, overflow: "auto" }}>
                  {openItems.data.map((it) => (
                    <label key={it.id} className="check" style={{ display: "flex", padding: "4px 0" }}>
                      <input type="checkbox" checked={bringIn.includes(it.id)} onChange={(e) => setBringIn((b) => (e.target.checked ? [...b, it.id] : b.filter((x) => x !== it.id)))} />
                      <TypeBadge type={it.type} /> <span>{it.title}</span>
                    </label>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}

        <div>
          <div className="section-label">Audio (optional)</div>
          <div className="stack">
            {devices.data?.error && <Banner kind="warn">{devices.data.error}</Banner>}
            <SourcePanel kind="mic" enabled={mic} setEnabled={setMic} device={micDev} setDevice={setMicDev} label={micLabel} setLabel={setMicLabel} devices={devices.data} />
            <SourcePanel kind="loopback" enabled={loop} setEnabled={setLoop} device={loopDev} setDevice={setLoopDev} label={loopLabel} setLabel={setLoopLabel} devices={devices.data} />
          </div>
        </div>

        {!resumeId && (
          <div className="card stack">
            <div className="grid-2">
              <div className="field">
                <label htmlFor="screen"><Monitor size={15} style={{ verticalAlign: -2 }} /> Screenshots capture</label>
                <select id="screen" className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
                  <option value="foreground">The display with the active app{fgName ? ` (now: ${fgName})` : ""}</option>
                  {monitors.data?.monitors.map((m) => <option key={m.id} value={m.id}>Always {m.name} — {m.width}×{m.height}</option>)}
                </select>
              </div>
              <div className="field">
                <span className="label">Transcription</span>
                <div className="seg" role="radiogroup" aria-label="Transcription timing">
                  <button role="radio" aria-checked={mode === "after"} className={mode === "after" ? "on" : ""} onClick={() => setMode("after")} disabled={!mic && !loop}>After session</button>
                  <button role="radio" aria-checked={mode === "live"} className={mode === "live" ? "on" : ""} onClick={() => setMode("live")} disabled={!mic && !loop}>Live</button>
                </div>
                <span className="hint">{!mic && !loop ? "No audio selected." : mode === "after" ? "Recommended: nothing heavy runs while you work." : "Transcribes in the background during the session (uses CPU)."}</span>
              </div>
            </div>
            <Toggle checked={askAlways} onChange={setAskAlways} label="Always ask for context on F8" hint="Otherwise, with audio recording, F8 just saves a marker and stays out of your way." />
            <Toggle checked={ai && aiAvailable} disabled={!aiAvailable} onChange={setAi} label="Organise with local AI after the session"
              hint={aiAvailable ? "Uses Ollama on this PC. Your screenshot notes become cards either way." : "Not configured — set it up in Settings → Local AI. Captures work fine without it."} />
          </div>
        )}

        <div className="card">
          {mic || loop ? (
            <Banner kind="warn">
              <p><b>Will record: {recordingSummary}.</b> Tell everyone on the call that you're recording.</p>
              <p className="small">F8 saves a screenshot and a timeline marker; <kbd>Shift+F8</kbd> always asks for a note.</p>
            </Banner>
          ) : (
            <Banner kind="info"><p><b>No audio selected — F8 will ask for a note.</b> Screenshots and notes are saved immediately either way.</p></Banner>
          )}
          <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => nav(-1)}>Cancel</button>
            <button className="btn primary lg" disabled={(!projectId && !resumeId) || start.isPending} onClick={() => start.mutate()}>
              <Play size={17} /> {resumeId ? "Resume session" : "Start session"}
            </button>
          </div>
        </div>
      </div>
      {newProject && <ProjectModal onClose={(p) => { setNewProject(false); if (p) setProjectId(p.id); }} />}
    </div>
  );
}
