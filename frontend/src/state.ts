import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "./api";
import type { AppState, Project } from "./types";
import { useToast } from "./components/ui";

/** Polled application state (session health, counts, worker state). */
export function useAppState() {
  return useQuery({
    queryKey: ["state"],
    queryFn: () => api.get<AppState>("/api/state"),
    refetchInterval: (q) => (q.state.data?.session.active ? 700 : 2500),
    refetchIntervalInBackground: false,
  });
}

/** Surfaces backend notices (capture failures, recovery, sync conflicts) as toasts. */
export function useNoticeToasts(state: AppState | undefined) {
  const toast = useToast();
  const seen = useRef<number | null>(null);
  const qc = useQueryClient();
  useEffect(() => {
    if (!state) return;
    const notices = state.notices;
    if (seen.current == null) {
      // Show only notices from the last minute on first load.
      const recent = notices.filter((n) => Date.now() / 1000 - n.at < 60);
      recent.forEach((n) => toast(n.level, n.message));
      seen.current = notices.length ? notices[notices.length - 1].id : 0;
      return;
    }
    const fresh = notices.filter((n) => n.id > (seen.current ?? 0));
    if (fresh.length) {
      fresh.forEach((n) => toast(n.level, n.message));
      seen.current = fresh[fresh.length - 1].id;
      qc.invalidateQueries({ queryKey: ["drafts"] });
      qc.invalidateQueries({ queryKey: ["timeline"] });
      qc.invalidateQueries({ queryKey: ["unfinished"] });
    }
  }, [state, toast, qc]);
}

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/api/projects") });
}
