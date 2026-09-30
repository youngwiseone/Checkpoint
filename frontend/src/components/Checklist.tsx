import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckSquare, Square, Sparkles, Trash2, X } from "lucide-react";
import { api } from "../api";
import type { ChecklistEntry } from "../types";
import { useToast } from "./ui";

export default function Checklist({ sessionId, editable = true }: { sessionId: string; editable?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["checklist", sessionId], queryFn: () => api.get<ChecklistEntry[]>(`/api/sessions/${sessionId}/checklist`) });
  const [text, setText] = useState("");
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["checklist", sessionId] });
    qc.invalidateQueries({ queryKey: ["recap", sessionId] });
    qc.invalidateQueries({ queryKey: ["items"] });
  };
  const add = useMutation({
    mutationFn: () => api.post(`/api/sessions/${sessionId}/checklist`, { text }),
    onSuccess: () => { setText(""); inv(); },
    onError: (e: Error) => toast("error", e.message),
  });
  const setState = useMutation({
    mutationFn: ({ id, state }: { id: string; state: string }) => api.patch(`/api/checklist/${id}`, { state }),
    onSuccess: inv,
    onError: (e: Error) => toast("error", e.message),
  });
  const dismissSuggestion = useMutation({
    mutationFn: (id: string) => api.patch(`/api/checklist/${id}`, { dismiss_suggestion: true }),
    onSuccess: inv,
  });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/api/checklist/${id}`), onSuccess: inv });
  const items = q.data ?? [];
  return (
    <div>
      {!items.length && <p className="muted small">No checks yet. Add things you want to verify — they work without AI.</p>}
      {items.map((e) => (
        <div key={e.id} className={`checklist-item ${e.state === "done" ? "done" : ""}`}>
          <button className="icon-btn" aria-label={e.state === "done" ? `Mark “${e.text}” not done` : `Mark “${e.text}” done`}
            onClick={() => setState.mutate({ id: e.id, state: e.state === "done" ? "open" : "done" })}>
            {e.state === "done" ? <CheckSquare size={18} color="var(--success)" /> : <Square size={18} />}
          </button>
          <div className="grow">
            <span className="txt">{e.text}</span>
            <div className="row wrap small muted" style={{ gap: 8 }}>
              {e.origin === "brought_in" && <span>From project items</span>}
              {e.origin === "during" && <span>Added during session</span>}
              {e.state === "outstanding" && <span className="badge warn">Kept for later</span>}
            </div>
            {e.suggested_done && e.state !== "done" && (
              <div className="row small" style={{ marginTop: 4, color: "var(--warn)" }}>
                <Sparkles size={14} /> Possibly completed — confirm?
                <button className="btn sm" onClick={() => setState.mutate({ id: e.id, state: "done" })}>Confirm done</button>
                <button className="btn ghost sm" onClick={() => dismissSuggestion.mutate(e.id)}><X size={13} /> Not done</button>
              </div>
            )}
          </div>
          {editable && e.origin !== "brought_in" && (
            <button className="icon-btn" aria-label={`Remove “${e.text}”`} onClick={() => remove.mutate(e.id)}><Trash2 size={15} /></button>
          )}
        </div>
      ))}
      {editable && (
        <form className="row" style={{ marginTop: 8 }} onSubmit={(ev) => { ev.preventDefault(); if (text.trim()) add.mutate(); }}>
          <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a check…" aria-label="Add a check" />
          <button className="btn" disabled={!text.trim() || add.isPending}>Add</button>
        </form>
      )}
    </div>
  );
}
