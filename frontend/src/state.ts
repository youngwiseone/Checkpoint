import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { api } from "./api";
import type { AppState, Project, Settings } from "./types";
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

// The project the whole UI is looking at. Remembered per browser; falls back to the last project used.
const PROJECT_KEY = "checkpoint.project";
const listeners = new Set<() => void>();
let chosen: string | null = (() => {
  try { return localStorage.getItem(PROJECT_KEY); } catch { return null; }
})();

export function setCurrentProject(id: string) {
  chosen = id;
  try { localStorage.setItem(PROJECT_KEY, id); } catch { /* per-viewer convenience only */ }
  listeners.forEach((l) => l());
}

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api.get<Settings>("/api/settings") });
}

/** [current project, setter]. Demo and archived projects are only chosen explicitly. */
export function useCurrentProject(): [Project | undefined, (id: string) => void] {
  const id = useSyncExternalStore(useCallback((cb: () => void) => { listeners.add(cb); return () => listeners.delete(cb); }, []), () => chosen);
  const { data: projects } = useProjects();
  const { data: settings } = useSettings();
  const visible = (projects ?? []).filter((p) => !p.archived);
  const project = visible.find((p) => p.id === id)
    ?? visible.find((p) => p.id === settings?.last_project_id)
    ?? visible.find((p) => !p.is_demo)
    ?? visible[0];
  return [project, setCurrentProject];
}
