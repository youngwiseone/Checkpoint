import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { X, AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import type { ItemType, SharingState, WorkStatus } from "../types";

// ---------------------------------------------------------------- formatting
export function fmtOffset(ms: number | null | undefined): string {
  if (ms == null) return "--:--";
  const neg = ms < 0;
  const s = Math.floor(Math.abs(ms) / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const out = h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return neg ? `-${out}` : out;
}

export function fmtDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });
}

export function fmtDuration(startIso: string, endIso?: string | null): string {
  const ms = (endIso ? new Date(endIso).getTime() : Date.now()) - new Date(startIso).getTime();
  const m = Math.max(0, Math.round(ms / 60000));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function isTypingTarget(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (el as HTMLElement).isContentEditable;
}

// ---------------------------------------------------------------- labels
export const TYPE_LABEL: Record<ItemType, string> = {
  bug: "Bug", improvement: "Improvement", idea: "Idea", task: "Task", question: "Question", note: "Note",
};
export const TYPES: ItemType[] = ["bug", "improvement", "idea", "task", "question", "note"];
export const WORK_LABEL: Record<WorkStatus, string> = { open: "Open", in_progress: "In progress", done: "Done", wont_do: "Won't do" };
export const WORK_STATUSES: WorkStatus[] = ["open", "in_progress", "done", "wont_do"];
export const SHARE_LABEL: Record<SharingState, string> = {
  local_only: "Local only", pending: "Pending sync", synced: "Synced", failed: "Sync failed", conflict: "Conflict",
};

export function TypeBadge({ type }: { type: ItemType }) {
  return <span className={`badge type-${type}`}>{TYPE_LABEL[type] ?? type}</span>;
}

export function WorkBadge({ status }: { status: WorkStatus }) {
  const cls = status === "done" ? "success" : status === "in_progress" ? "accent" : status === "wont_do" ? "neutral" : "outline";
  return <span className={`badge ${cls}`}>{WORK_LABEL[status]}</span>;
}

export function ShareBadge({ state, error }: { state: SharingState; error?: string | null }) {
  const cls = state === "synced" ? "success" : state === "pending" ? "accent" : state === "failed" ? "danger" : state === "conflict" ? "warn" : "neutral";
  const label = state === "pending" && error ? "Saved locally — waiting to sync" : SHARE_LABEL[state];
  return <span className={`badge ${cls}`} title={error ?? undefined}>{label}</span>;
}

// ---------------------------------------------------------------- primitives
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row" role="status">
      <span className="spinner" aria-hidden />
      {label && <span className="muted">{label}</span>}
    </span>
  );
}

export function Toggle({ checked, onChange, label, disabled, hint }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; disabled?: boolean; hint?: ReactNode }) {
  return (
    <label className="toggle" style={disabled ? { cursor: "not-allowed" } : undefined}>
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" aria-hidden />
      <span>
        <span className="label">{label}</span>
        {hint && <span className="hint" style={{ display: "block" }}>{hint}</span>}
      </span>
    </label>
  );
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      {icon && <div className="icon">{icon}</div>}
      <h3>{title}</h3>
      {children && <div className="text-2" style={{ maxWidth: 520, margin: "0 auto" }}>{children}</div>}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

export function Banner({ kind, children, action, icon }: { kind: "info" | "warn" | "error" | "success"; children: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  const Icon = kind === "error" ? XCircle : kind === "warn" ? AlertTriangle : kind === "success" ? CheckCircle2 : Info;
  const color = kind === "error" ? "var(--danger)" : kind === "warn" ? "var(--warn)" : kind === "success" ? "var(--success)" : "var(--accent)";
  return (
    <div className={`banner ${kind}`} role={kind === "error" ? "alert" : "status"}>
      <span style={{ color, marginTop: 2 }}>{icon ?? <Icon size={18} />}</span>
      <div className="grow">{children}</div>
      {action}
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input, textarea, select, button:not(.icon-btn)");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      prev?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined} ref={ref}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && (e.stopPropagation(), onClose());
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  return (
    <div className="lightbox" onClick={onClose} role="dialog" aria-label="Screenshot">
      <img src={src} alt="Screenshot" />
    </div>
  );
}

export function Meter({ value, active }: { value: number; active: boolean }) {
  return (
    <div className={`meter ${active ? "" : "off"}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)} aria-label="Audio level">
      <div style={{ width: `${Math.round(Math.min(1, value) * 100)}%` }} />
    </div>
  );
}

// ---------------------------------------------------------------- toasts
type ToastItem = { id: number; kind: "info" | "success" | "warning" | "error"; text: string };
const ToastCtx = createContext<(kind: ToastItem["kind"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(1);
  const push = useCallback((kind: ToastItem["kind"], text: string) => {
    const id = idRef.current++;
    setItems((xs) => [...xs.slice(-3), { id, kind, text }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className="toast">
            <span className={`dot ${t.kind === "success" ? "ok" : t.kind === "error" ? "bad" : t.kind === "warning" ? "warn" : "info"}`} />
            <div className="grow">{t.text}</div>
            <button className="icon-btn" aria-label="Dismiss" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}><X size={14} /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

// ---------------------------------------------------------------- autosave
export type SaveState = "idle" | "saving" | "saved" | "error";

/** Debounced autosave. "Saved" is only shown after the server confirms. */
export function useAutosave<T>(save: (value: T) => Promise<unknown>, delay = 600) {
  const [state, setState] = useState<SaveState>("idle");
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const pending = useRef<T | null>(null);
  const saveRef = useRef(save);
  saveRef.current = save;

  const flush = useCallback(async () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    const v = pending.current;
    if (v == null) return;
    pending.current = null;
    setState("saving");
    try {
      await saveRef.current(v);
      setState(pending.current == null ? "saved" : "saving");
      setError(null);
    } catch (e) {
      pending.current = pending.current ?? v;
      setState("error");
      setError((e as Error).message);
    }
  }, []);

  const schedule = useCallback((v: T) => {
    pending.current = v;
    setState("saving");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, delay);
  }, [delay, flush]);

  useEffect(() => () => { if (timer.current) { window.clearTimeout(timer.current); void flush(); } }, [flush]);
  return { state, error, schedule, flush };
}

export function SaveIndicator({ state, error, onRetry }: { state: SaveState; error: string | null; onRetry: () => void }) {
  if (state === "saving") return <Spinner label="Saving…" />;
  if (state === "saved") return <span className="row small" style={{ color: "var(--success)" }}><CheckCircle2 size={15} /> Saved</span>;
  if (state === "error")
    return (
      <span className="row small" style={{ color: "var(--danger)" }} role="alert">
        <XCircle size={15} /> Not saved: {error}
        <button className="btn sm" onClick={onRetry}>Retry</button>
      </span>
    );
  return null;
}
