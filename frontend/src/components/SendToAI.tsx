import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, Check, ClipboardCopy, FolderOpen, Send } from "lucide-react";
import { api } from "../api";
import { Banner, Modal, useToast } from "./ui";

export type Integration = { app: "claude" | "codex"; name: string; installed: boolean; connected: boolean; config: string };

export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Fallback when the Clipboard API is unavailable (e.g. page not focused).
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) throw new Error("Couldn't copy to the clipboard");
  }
}

type Mode = "implement_commit" | "implement" | "investigate" | "read";
const MODES: { v: Mode; label: string; hint: string }[] = [
  { v: "implement_commit", label: "Implement & commit", hint: "Fix the bugs / build the features, check they work, then commit each change." },
  { v: "implement", label: "Implement only", hint: "Fix / build and check it works, but leave the changes uncommitted for you to review." },
  { v: "investigate", label: "Investigate & plan", hint: "Find causes and propose fixes or implementation plans without changing files." },
  { v: "read", label: "Just read", hint: "Only share the items; you'll say what to do." },
];

function savedMode(): Mode {
  try {
    const m = localStorage.getItem("checkpoint.ai-mode") as Mode | null;
    return m && MODES.some((x) => x.v === m) ? m : "implement_commit";
  } catch {
    return "implement_commit";
  }
}

function savedFlag(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* per-viewer convenience only */ }
}

export default function SendToAIModal({ ids, onClose, onSent }: { ids: string[]; onClose: () => void; onSent?: () => void }) {
  const [mode, setModeState] = useState<Mode>(savedMode);
  const [shots, setShotsState] = useState(() => savedFlag("checkpoint.ai-shots", true));
  const [excerpts, setExcerptsState] = useState(() => savedFlag("checkpoint.ai-excerpts", true));
  const [instruction, setInstruction] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const setMode = (m: Mode) => { setModeState(m); setCopied(null); remember("checkpoint.ai-mode", m); };
  const setShots = (v: boolean) => { setShotsState(v); setCopied(null); remember("checkpoint.ai-shots", v ? "1" : "0"); };
  const setExcerpts = (v: boolean) => { setExcerptsState(v); setCopied(null); remember("checkpoint.ai-excerpts", v ? "1" : "0"); };
  const toast = useToast();
  const integrations = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<Integration[]>("/api/integrations") });
  const connected = (integrations.data ?? []).filter((i) => i.connected);
  const body = { ids, include_screenshots: shots, include_excerpts: excerpts, instruction, mode };
  const n = ids.length;

  const send = useMutation({
    mutationFn: () => api.post<{ code: string; prompt: string }>("/api/handoffs", body),
    onSuccess: async (r) => {
      try {
        await copyText(r.prompt);
        setCopied(r.code);
        onSent?.();
      } catch (e) {
        toast("error", (e as Error).message);
      }
    },
    onError: (e: Error) => toast("error", e.message),
  });
  const copyPlain = useMutation({
    mutationFn: () => api.post<{ markdown: string }>("/api/ai-copy", body),
    onSuccess: async (r) => {
      await copyText(r.markdown);
      toast("success", "Text copied. Paste it into any AI chat.");
    },
    onError: (e: Error) => toast("error", e.message),
  });
  const folder = useMutation({
    mutationFn: () => api.post<{ folder: string }>("/api/ai-copy/folder", body),
    onSuccess: () => toast("success", "Opened a folder with items.md and the screenshots. Drag them into the chat."),
    onError: (e: Error) => toast("error", e.message),
  });

  return (
    <Modal title={`Send ${n} item${n === 1 ? "" : "s"} to Claude / Codex`} onClose={onClose} footer={<button className="btn" onClick={onClose}>{copied ? "Done" : "Close"}</button>}>
      <div className="stack">
        {integrations.data && connected.length === 0 && (
          <Banner kind="info">Connect Claude Desktop or Codex once in <Link to="/settings?tab=assistants" onClick={onClose}>Settings → AI assistants</Link> so they can fetch the items and screenshots themselves.</Banner>
        )}
        <div className="field">
          <span className="label">What should it do?</span>
          <div className="seg" role="radiogroup" aria-label="What should the AI do?">
            {MODES.map((m) => (
              <button key={m.v} role="radio" aria-checked={mode === m.v} className={mode === m.v ? "on" : ""} onClick={() => setMode(m.v)}>{m.label}</button>
            ))}
          </div>
          <span className="hint">{MODES.find((m) => m.v === mode)?.hint}{n > 1 && mode !== "read" ? ` It works through all ${n} in order and reports back at the end.` : ""}</span>
        </div>
        <div className="row wrap">
          <button className="btn primary lg" onClick={() => send.mutate()} disabled={send.isPending}><Send size={16} /> Copy prompt</button>
          {copied ? <span className="row small" style={{ color: "var(--success)" }}><Check size={15} /> Copied {copied}. Paste it into {connected.map((c) => c.name).join(" or ") || "Claude Desktop or Codex"} (Ctrl+V).</span>
            : connected.length > 0 && <span className="badge success"><Bot size={12} /> {connected.map((c) => c.name).join(", ")}</span>}
        </div>
        <details>
          <summary className="label" style={{ cursor: "pointer" }}>Options</summary>
          <div className="stack" style={{ marginTop: 10 }}>
            <div className="row wrap" style={{ gap: 20 }}>
              <label className="check"><input type="checkbox" checked={shots} onChange={(e) => setShots(e.target.checked)} /> Include screenshots</label>
              <label className="check"><input type="checkbox" checked={excerpts} onChange={(e) => setExcerpts(e.target.checked)} /> Include what was said/typed</label>
            </div>
            <div className="field">
              <label htmlFor="ai-instr">Anything else? <span className="muted">(optional)</span></label>
              <textarea id="ai-instr" className="textarea" rows={2} value={instruction} onChange={(e) => { setInstruction(e.target.value); setCopied(null); }}
                placeholder="e.g. The death replay code is in scripts/rpg/, or: keep the new menu consistent with the settings screen." />
            </div>
          </div>
        </details>
        <details>
          <summary className="label" style={{ cursor: "pointer" }}>Another AI (no Checkpoint connector)</summary>
          <div className="row wrap" style={{ marginTop: 10 }}>
            <button className="btn" onClick={() => copyPlain.mutate()} disabled={copyPlain.isPending}><ClipboardCopy size={15} /> Copy as text</button>
            {shots && <button className="btn" onClick={() => folder.mutate()} disabled={folder.isPending}><FolderOpen size={15} /> Open screenshots folder</button>}
          </div>
        </details>
        <p className="hint">Nothing is uploaded by Checkpoint. Audio and full transcripts are never included.</p>
      </div>
    </Modal>
  );
}
