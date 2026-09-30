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

export default function SendToAIModal({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const [mode, setModeState] = useState<Mode>(savedMode);
  const setMode = (m: Mode) => {
    setModeState(m);
    setCopied(null);
    try { localStorage.setItem("checkpoint.ai-mode", m); } catch { /* per-viewer convenience only */ }
  };
  const [shots, setShots] = useState(true);
  const [excerpts, setExcerpts] = useState(true);
  const [instruction, setInstruction] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const toast = useToast();
  const integrations = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<Integration[]>("/api/integrations") });
  const connected = (integrations.data ?? []).filter((i) => i.connected);
  const body = { ids, include_screenshots: shots, include_excerpts: excerpts, instruction, mode };

  const send = useMutation({
    mutationFn: () => api.post<{ code: string; prompt: string }>("/api/handoffs", body),
    onSuccess: async (r) => {
      try {
        await copyText(r.prompt);
        setCopied(r.code);
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
    <Modal title={`Send ${ids.length} item${ids.length === 1 ? "" : "s"} to an AI assistant`} onClose={onClose} footer={<button className="btn" onClick={onClose}>Close</button>}>
      <div className="stack">
        <div className="row wrap" style={{ gap: 20 }}>
          <label className="check"><input type="checkbox" checked={shots} onChange={(e) => setShots(e.target.checked)} /> Include screenshots</label>
          <label className="check"><input type="checkbox" checked={excerpts} onChange={(e) => setExcerpts(e.target.checked)} /> Include what was said/typed</label>
        </div>
        <div className="field">
          <span className="label">What should the AI do?</span>
          <div className="seg" role="radiogroup" aria-label="What should the AI do?">
            {MODES.map((m) => (
              <button key={m.v} role="radio" aria-checked={mode === m.v} className={mode === m.v ? "on" : ""} onClick={() => setMode(m.v)}>{m.label}</button>
            ))}
          </div>
          <span className="hint">{MODES.find((m) => m.v === mode)?.hint}</span>
        </div>
        <div className="field">
          <label htmlFor="ai-instr">Anything else? <span className="muted">(optional)</span></label>
          <textarea id="ai-instr" className="textarea" rows={2} value={instruction} onChange={(e) => { setInstruction(e.target.value); setCopied(null); }}
            placeholder="e.g. The death replay code is in scripts/rpg/, or: keep the new menu consistent with the settings screen." />
        </div>

        <div className="card stack">
          <div className="row"><Bot size={18} className="muted" /><b className="grow">Claude Desktop / Codex</b>
            {connected.length > 0 && <span className="badge success">Connected: {connected.map((c) => c.name).join(", ")}</span>}</div>
          {integrations.data && connected.length === 0 && (
            <Banner kind="info">Connect Claude Desktop or Codex once in <Link to="/settings?tab=assistants" onClick={onClose}>Settings → AI assistants</Link>. They can then fetch these items and screenshots themselves.</Banner>
          )}
          <p className="small text-2">Copies a short prompt. Paste it into Claude Desktop or Codex (Ctrl+V); the assistant fetches the items and screenshots from Checkpoint on this PC.</p>
          <div className="row">
            <button className="btn primary" onClick={() => send.mutate()} disabled={send.isPending}><Send size={15} /> Copy prompt for Claude / Codex</button>
            {copied && <span className="row small" style={{ color: "var(--success)" }}><Check size={15} /> Copied handoff {copied}. Paste it into the chat.</span>}
          </div>
        </div>

        <div className="card stack">
          <b>Any other AI</b>
          <p className="small text-2">For chats without the Checkpoint connector: copy the text, then drag in the screenshots from the folder.</p>
          <div className="row wrap">
            <button className="btn" onClick={() => copyPlain.mutate()} disabled={copyPlain.isPending}><ClipboardCopy size={15} /> Copy as text</button>
            {shots && <button className="btn" onClick={() => folder.mutate()} disabled={folder.isPending}><FolderOpen size={15} /> Open screenshots folder</button>}
          </div>
        </div>
        <p className="hint">Nothing is uploaded by Checkpoint. Audio and full transcripts are never included.</p>
      </div>
    </Modal>
  );
}
