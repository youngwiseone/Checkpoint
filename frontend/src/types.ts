export type ItemType = "bug" | "improvement" | "idea" | "task" | "question" | "note";
export type ReviewState = "pending" | "approved" | "dismissed";
export type WorkStatus = "open" | "in_progress" | "done" | "wont_do";
export type SharingState = "local_only" | "pending" | "synced" | "failed" | "conflict";

export interface Notice {
  id: number;
  level: "info" | "success" | "warning" | "error";
  message: string;
  source: string;
  at: number;
}

export interface SourceStatus {
  source_id: string;
  kind: "mic" | "loopback";
  label: string;
  device: string;
  state: "starting" | "recording" | "idle" | "paused" | "reconnecting" | "failed" | "stopped";
  receiving: boolean;
  level: number;
  signal_recent: boolean;
  error: string | null;
  interrupted_at_ms: number | null;
  attempt: number;
  dropped_buffers: number;
  writer_alive: boolean;
}

export interface ActiveSessionStatus {
  active: boolean;
  session_id?: string;
  project_id?: string;
  title?: string;
  paused?: boolean;
  offset_ms?: number;
  started_at?: string;
  transcription_mode?: string;
  ai_enabled?: boolean;
  audio_functioning?: boolean;
  always_ask_context?: boolean;
  capture_target?: string;
  sources?: SourceStatus[];
}

export interface HotkeyBinding {
  action: string;
  keys: string;
  registered: boolean;
  error: string | null;
}

export interface SessionOffer {
  project_id: string;
  project_name: string;
  program: string;
  at: number;
}

export interface WatchRule {
  project_id: string;
  kind: "exe" | "title";
  match: string;
}

export interface SessionSetup {
  project_id: string;
  mic: { enabled: boolean; device: string | null; label: string };
  loopback: { enabled: boolean; device: string | null; label: string };
  capture_target: string;
  transcription_mode: "live" | "after" | "off";
  ai_enabled: boolean;
  always_ask_context: boolean;
}

export interface AppState {
  version: string;
  first_run_complete: boolean;
  session: ActiveSessionStatus;
  review: { pending: number; approved: number; dismissed: number; unfinished_captures: number };
  workers: {
    transcription: { alive: boolean; busy: boolean; paused: boolean; model: string; model_installed: boolean; error: string | null; gpu_fallback: string | null; stalled?: boolean; device?: string };
    organiser: { alive: boolean; busy: boolean; current_run: string | null; enabled: boolean; error: string | null };
    auto_capture: { alive: boolean; enabled: boolean; state: "off" | "needs_live" | "watching"; count: number; error: string | null };
    app_watch: { alive: boolean; offer: SessionOffer | null; running: string[]; error: string | null };
    sync: { alive: boolean; busy: boolean; offline_reason: string | null; last_ok: string | null; configured: boolean };
  };
  desktop: { available: boolean; hotkeys: { ok?: boolean; bindings?: HotkeyBinding[]; reason?: string | null } };
  notices: Notice[];
  ai_enabled: boolean;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  glossary: string;
  is_demo: boolean;
  archived: boolean;
  shared_project_id: string | null;
  shared_project_name: string | null;
  shared_server_url: string | null;
  last_refreshed_at: string | null;
  session_count: number;
  open_items: number;
  pending_cards: number;
  repo_path: string | null;
  base_branch: string | null;
  default_agent: AgentId | "none";
  agent_access: "standard" | "full";
  setup_command: string;
  check_command: string;
  preview_command: string;
  preview_url: string;
  configured: boolean;
}

export interface SessionSummary {
  id: string;
  project_id: string;
  title: string;
  purpose: string;
  state: "active" | "paused" | "ended" | "interrupted";
  started_at: string;
  ended_at: string | null;
  last_heartbeat_at: string | null;
  transcription_mode: "live" | "after" | "off";
  ai_enabled: boolean;
  processing_state: string;
  capture_count: number;
  pending_cards: number;
  card_count: number;
  is_demo: boolean;
  raw_audio_deleted: boolean;
  sources: { id: string; kind: "mic" | "loopback"; label: string; device: string; state: string; last_error: string | null }[];
  transcription: { queued: number; running: number; done: number; failed: number };
}

export interface ProcessingRun {
  id: string;
  session_id: string;
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  model: string;
  prompt_version: string;
  progress: number;
  stage: string;
  error: string | null;
  stats: Record<string, unknown>;
  created_at: string;
  finished_at: string | null;
}

export interface SessionDetail extends SessionSummary {
  pauses: { start_ms: number; end_ms: number | null; reason: string }[];
  audio_events: { source_id: string; kind: string; offset_ms: number; message: string }[];
  latest_run: ProcessingRun | null;
  transcription_errors: string[];
  project_name: string;
  audio_seconds: number;
}

export interface Evidence {
  id: string;
  kind: "capture" | "segment" | "note";
  confidence: "direct" | "candidate" | "uncertain";
  role: "source" | "withdrawal" | "correction" | "completion" | "context";
  attached_by: "system" | "ai" | "user";
  offset_ms: number | null;
  // capture
  capture_id?: string;
  thumb_url?: string;
  image_url?: string;
  window_title?: string;
  reason?: string | null;
  // segment
  segment_id?: string;
  end_ms?: number;
  text?: string;
  raw_text?: string;
  corrected?: boolean;
  source_label?: string;
  low_confidence?: boolean;
  audio_url?: string | null;
  // note
  note_id?: string;
  note_kind?: string;
}

export interface Draft {
  id: string;
  project_id: string;
  session_id: string | null;
  session_title?: string | null;
  origin: string;
  type: ItemType;
  title: string;
  description: string;
  review_state: ReviewState;
  needs_context: boolean;
  uncertainty: string | null;
  statement_kind: string | null;
  withdrawn: boolean;
  possibly_completed: boolean;
  conflict_note: string | null;
  human_edited: boolean;
  work_item_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  evidence: Evidence[];
  offset_ms: number | null;
}

export interface WorkItem {
  id: string;
  project_id: string;
  session_id: string | null;
  session_title?: string | null;
  type: ItemType;
  title: string;
  description: string;
  work_status: WorkStatus;
  assignee: string | null;
  priority: string | null;
  tags: string[];
  version: number;
  origin: "local" | "shared";
  created_by: string | null;
  updated_by: string | null;
  sharing_state: SharingState;
  server_version: number | null;
  sync_error: string | null;
  conflict_server_copy: Record<string, unknown> | null;
  shared_attachment_ids: string[];
  shared_excerpts: boolean;
  last_synced_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  capture_count: number;
  thumb_url?: string | null;
  evidence?: Evidence[];
  remote_attachments?: { id: string; url: string }[];
}

export interface ChecklistEntry {
  id: string;
  session_id: string;
  text: string;
  work_item_id: string | null;
  origin: "planned" | "during" | "brought_in";
  state: "open" | "done" | "outstanding";
  suggested_done: boolean;
  suggestion_segment_ids: string[];
  position: number;
  completed_at: string | null;
}

export interface TimelineItem {
  kind: "segment" | "note" | "capture" | "pause" | "audio_event";
  id: string;
  offset_ms: number | null;
  end_ms?: number | null;
  text?: string;
  raw_text?: string;
  corrected?: boolean;
  source_label?: string;
  source_kind?: string;
  low_confidence?: boolean;
  audio_url?: string | null;
  note_kind?: string;
  capture_id?: string | null;
  category?: string | null;
  status?: string;
  window_title?: string;
  thumb_url?: string;
  image_url?: string;
  context_state?: string;
  trigger?: string;
  reason?: string | null;
  event?: string;
  message?: string;
}

export interface Capture {
  id: string;
  session_id: string | null;
  project_id: string;
  offset_ms: number | null;
  taken_at: string;
  status: string;
  trigger?: string;
  window_title: string;
  thumb_url: string;
  image_url: string;
  context_state?: string;
  nearby?: { id: string; offset_ms: number; text: string }[];
}

export interface ReadinessCheck {
  id: string;
  label: string;
  state: "ready" | "warning" | "unavailable" | "error";
  detail: string;
  remedy?: string | null;
}

export interface AudioDevices {
  mic: { name: string; channels: number; sample_rate: number }[];
  loopback: { name: string; channels: number; sample_rate: number; output_name: string }[];
  default_mic: string | null;
  default_loopback: string | null;
  error?: string;
}

export interface Settings {
  first_run_complete: boolean;
  hotkeys: { capture: string; capture_context: string; quick_note: string; start_session: string; review: string };
  capture: { always_ask_context: boolean; region: "foreground_monitor" | "foreground_window"; debounce_ms: number };
  audio: {
    mic_enabled: boolean;
    mic_device: string | null;
    mic_label: string;
    loopback_enabled: boolean;
    loopback_device: string | null;
    loopback_label: string;
    chunk_seconds: number;
  };
  transcription: { model: string; device: "cpu" | "cuda"; compute_type: string; default_mode: "after" | "live" | "off"; paused: boolean; cpu_threads: number; overlap_seconds: number };
  ai: { enabled: boolean; base_url: string; model: string; timeout_seconds: number; max_retries: number; chunk_chars: number; chunk_overlap_items: number; window_before_s: number; window_after_s: number };
  auto_capture: { enabled: boolean; model: string; threshold: number; frame_interval_s: number; buffer_seconds: number; cooldown_s: number; max_per_session: number; lead_s: number };
  app_watch: { enabled: boolean; rules: WatchRule[] };
  sharing: { server_url: string; display_name: string; allow_insecure_private_network: boolean };
  agents: { claude_path: string; codex_path: string };
  last_project_id: string | null;
  data_dir: string;
}

// ---------------------------------------------------------------- coding agents
export type AgentId = "claude" | "codex";

export interface AgentInfo {
  id: AgentId;
  name: string;
  path: string | null;
}

export interface RepoInspect {
  root: string;
  current_branch: string | null;
  default_branch: string | null;
  remote_url: string | null;
  branches: string[];
}

export type FlowTaskState = "approved" | "sending" | "sent" | "working" | "checking" | "ready" | "needs_you" | "merged";

export interface FlowTask {
  id: string;
  code: string;
  title: string;
  type: ItemType;
  state: FlowTaskState;
  label: string | null;
  checking: boolean;
  note: string | null;
  commit: string | null;
  live: boolean;
  duplicate_of: string | null;
  run_id: string | null;
  sent_at: string | null;
  work_status: WorkStatus | null;
}

export interface FlowRun {
  id: string;
  state: "queued" | "starting" | "running" | "checking" | "done" | "failed" | "cancelled";
  stage: string | null;
  agent: string;
  agent_label: string;
  items: number;
  summary: string | null;
  error: string | null;
  log_path: string | null;
  created_at: string | null;
  accepted_at: string | null;
  finished_at: string | null;
}

export interface PreviewStatus {
  state: "starting" | "running" | "failed" | "stopped";
  commit: string | null;
  url: string | null;
  error: string | null;
  log?: string | null;
}

export interface SessionFlow {
  session: {
    id: string;
    title: string;
    name_locked: boolean;
    name_edited: boolean;
    branch: string | null;
    worktree_path: string | null;
    pr_url: string | null;
    state: string;
    project_id: string;
  };
  project: {
    id: string;
    name: string;
    agent: AgentId | "none";
    agent_label: string;
    repo_path: string | null;
    base_branch: string | null;
    has_preview: boolean;
    problems: string[];
  };
  counts: Partial<Record<FlowTaskState, number>> & { pending: number };
  tasks: FlowTask[];
  runs: FlowRun[];
  preview: PreviewStatus | null;
  ready_to_refresh: boolean;
  cards: unknown[];
  approved_unsent: number;
}

export interface SendPlan {
  count: number;
  tasks: { id: string; title: string; type: ItemType; follow_up_of: string | null }[];
  merges: { id: string; title: string; into: string; into_title: string }[];
  agent: AgentId | "none";
  agent_label: string;
  branch: string;
  branch_new: boolean;
  name: string;
  repo: string | null;
  queued_behind_current: boolean;
  problems: string[];
}
