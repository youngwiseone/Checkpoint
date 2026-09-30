import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, RefreshCw, Sparkles } from "lucide-react";
import { api } from "../api";
import type { ReadinessCheck } from "../types";
import { Spinner, useToast } from "../components/ui";

export function ReadinessList({ compact }: { compact?: boolean }) {
  const q = useQuery({ queryKey: ["readiness"], queryFn: () => api.get<{ checks: ReadinessCheck[] }>("/api/readiness"), staleTime: 10_000 });
  if (q.isLoading) return <Spinner label="Checking what's available…" />;
  if (q.error) return <p style={{ color: "var(--danger)" }}>{(q.error as Error).message}</p>;
  return (
    <div className="readiness">
      {q.data!.checks.map((c) => (
        <div className="item" key={c.id}>
          <span className={`dot ${c.state === "ready" ? "ok" : c.state === "warning" ? "warn" : c.state === "error" ? "bad" : "off"}`} aria-hidden />
          <div>
            <div className="row">
              <b>{c.label}</b>
              <span className="muted small">
                {c.state === "ready" ? "Ready" : c.state === "warning" ? "Needs attention" : c.state === "error" ? "Not working" : "Not set up (optional)"}
              </span>
            </div>
            <div className="text-2 small">{c.detail}</div>
            {!compact && c.remedy && <div className="small" style={{ marginTop: 4 }}>{c.remedy}</div>}
          </div>
        </div>
      ))}
      <div>
        <button className="btn ghost sm" onClick={() => q.refetch()} disabled={q.isFetching}><RefreshCw size={14} /> Check again</button>
      </div>
    </div>
  );
}

export default function Welcome() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const finish = useMutation({
    mutationFn: async (opts: { demo?: boolean; settings?: boolean }) => {
      await api.patch("/api/settings", { first_run_complete: true, ...(opts.settings ? {} : { ai: { enabled: false } }) });
      if (opts.demo) await api.post("/api/demo");
      return opts;
    },
    onSuccess: (opts) => {
      qc.invalidateQueries();
      nav(opts.settings ? "/settings" : "/");
    },
    onError: (e: Error) => toast("error", e.message),
  });
  return (
    <div className="page" style={{ maxWidth: 860 }}>
      <div className="hero stack-lg">
        <div>
          <h1>Checkpoint</h1>
          <p className="tag">Capture the moment. Keep the context. Turn it into action.</p>
        </div>
        <p className="text-2">
          Start a session, press <kbd>F8</kbd> to grab a screenshot of what you're looking at, <kbd>F9</kbd> for a quick text note,
          and review everything as tidy cards afterwards. Microphone, computer audio, transcription and local AI are all optional —
          screenshots and notes always work, offline, on this PC.
        </p>
        <div className="row wrap">
          <button className="btn primary lg" onClick={() => finish.mutate({})} disabled={finish.isPending}>
            Start without AI <ArrowRight size={18} />
          </button>
          <button className="btn lg" onClick={() => finish.mutate({ settings: true })} disabled={finish.isPending}>
            <Sparkles size={17} /> Set up optional features
          </button>
          <button className="btn ghost" onClick={() => finish.mutate({ demo: true })} disabled={finish.isPending}>
            Start with a demo project
          </button>
        </div>
      </div>
      <div className="card" style={{ marginTop: 22 }}>
        <div className="card-head"><h2>What's available on this PC</h2></div>
        <ReadinessList />
      </div>
      <p className="muted small" style={{ marginTop: 16 }}>
        Nothing is recorded until you start a session and switch a source on. No telemetry, no cloud account, no paid AI.
      </p>
    </div>
  );
}
