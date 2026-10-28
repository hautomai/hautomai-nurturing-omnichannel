/**
 * Hand-written mirrors of the backend Pydantic schemas (`backend/app/schemas.py`).
 *
 * Deliberately hand-written rather than generated: the surface is small, and a
 * generated client would need a build step before the app can run at all. Keep
 * this file in sync when a schema changes.
 */

export type Channel = "email" | "sms" | "slack";

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
}

export interface Client {
  id: string;
  name: string;
  slug: string;
  default_timezone: string;
  sending_halted: boolean;
  halt_reason: string | null;
}

export interface Persona {
  id: string;
  client_id: string;
  display_name: string;
  from_name: string;
  from_email: string;
  reply_to: string;
  signature_html: string;
  calendar_link: string;
  is_default: boolean;
}

export type InboxStatus = "active" | "warming" | "paused";

export interface Inbox {
  id: string;
  client_id: string;
  email: string;
  display_name: string;
  status: InboxStatus;
  daily_cap: number;
}

export interface PhoneNumber {
  id: string;
  client_id: string;
  e164: string;
  provider: string;
  daily_cap: number;
  status: InboxStatus;
}

export interface Template {
  id: string;
  client_id: string;
  key: string;
  channel: "email" | "sms";
  name: string;
  subject: string;
  body: string;
}

export type ContactSource = "manual" | "csv" | "ghl" | "api" | "webhook";

export interface Contact {
  id: string;
  client_id: string;
  email: string | null;
  phone_e164: string | null;
  first_name: string;
  last_name: string;
  company: string;
  job_title: string;
  timezone: string;
  source: ContactSource;
  tags: string[];
  custom_fields: Record<string, unknown>;
  created_at: string;
}

export interface ImportResult {
  created: number;
  updated: number;
  rejected: { row?: string; reason: string }[];
  enrolled: number;
}

/* -------------------------------------------------------------------------
 * Sequences
 * ---------------------------------------------------------------------- */

export type NodeType =
  | "send_email"
  | "send_sms"
  | "delay"
  | "condition"
  | "notify_slack"
  | "human_task"
  | "exit";

export type FallbackMode = "skip" | "send_email" | "send_sms";

export interface StepNode {
  key: string;
  type: NodeType;
  name: string;
  template_key?: string;
  fallback?: FallbackMode;
  fallback_template_key?: string;
  /** Applies to delay nodes. */
  amount?: number;
  unit?: "minutes" | "hours" | "days";
  /** Applies to condition nodes. */
  predicate?: Record<string, unknown>;
  on_true?: string;
  on_false?: string;
  /** Applies to notify_slack / human_task nodes. */
  text?: string;
  title?: string;
  instructions?: string;
  on_timeout?: string;
  timeout_hours?: number;
  /** Applies to exit nodes. */
  reason?: string;
  /** Every non-terminal node points at its successor. */
  next?: string;
}

export interface SequenceGraph {
  entry_step_keys: string[];
  steps: StepNode[];
  reactions?: Record<string, string>;
}

export type SequenceStatus = "draft" | "active" | "paused" | "archived";

export interface Sequence {
  id: string;
  client_id: string;
  name: string;
  description: string;
  status: SequenceStatus;
  current_version_id: string | null;
  reaction_policy: string;
  allow_cadence_override: boolean;
  cadence_preset: string;
  created_at: string;
  updated_at: string;
}

export interface SequenceVersion {
  id: string;
  sequence_id: string;
  version: number;
  graph: SequenceGraph;
  notes: string;
  created_at: string;
  published_at: string | null;
}

export interface SequenceDetail {
  sequence: Sequence;
  current_version: SequenceVersion | null;
  draft_version: SequenceVersion | null;
  versions: SequenceVersion[];
}

export interface ValidationIssue {
  level: "error" | "warning";
  step_key: string | null;
  code: string;
  message: string;
}

export interface ValidateResult {
  ok: boolean;
  issues: ValidationIssue[];
}

export interface PublishResult {
  ok: boolean;
  issues: ValidationIssue[];
  version: SequenceVersion | null;
  migrated_enrollments: number;
  orphaned_enrollments: number;
}

/* -------------------------------------------------------------------------
 * Enrollments
 * ---------------------------------------------------------------------- */

export type EnrollmentStatus =
  | "pending"
  | "active"
  | "paused"
  | "completed"
  | "exited"
  | "failed";

export interface Enrollment {
  id: string;
  contact_id: string;
  client_id: string;
  sequence_id: string;
  sequence_version_id: string;
  status: EnrollmentStatus;
  channel_lane: string;
  current_step_key: string | null;
  next_run_at: string | null;
  exit_reason: string | null;
  pause_reason: string | null;
  touches: number;
  sms_touches: number;
  entered_at: string;
  last_event_at: string | null;
}

/* -------------------------------------------------------------------------
 * Simulation
 * ---------------------------------------------------------------------- */

export interface SimulatedStep {
  step_key: string;
  step_type: string;
  channel: string;
  run_at: string | null;
  detail: string;
  rendered_subject: string;
  rendered_body: string;
  outcome: string;
}

export interface SimulationResult {
  timeline: SimulatedStep[];
  exited: boolean;
  exit_reason: string | null;
  warnings: string[];
}

/* -------------------------------------------------------------------------
 * Timeline & analytics
 * ---------------------------------------------------------------------- */

export type TimelineKind = "event" | "message" | "inbound" | "decision" | "task";

export interface TimelineEntry {
  kind: TimelineKind;
  at: string;
  title: string;
  channel: string;
  status: string;
  detail: Record<string, unknown>;
}

export interface FunnelStep {
  step_key: string;
  name: string;
  channel: string;
  index: number;
  sent: number;
  delivered: number;
  bounced: number;
  failed: number;
  skipped: number;
  substituted: number;
  replied: number;
  positive_replies: number;
  meetings: number;
}

export interface OverviewMetrics {
  active_enrollments: number;
  sent_today: number;
  daily_capacity: number;
  reply_rate: number;
  replies: number;
  positive_replies: number;
  meetings: number;
  live_versions: number;
  deltas: Record<string, number>;
}

export interface ChannelComparisonRow {
  channel: string;
  sent: number;
  delivered: number;
  replies: number;
  reply_rate: number;
  positive_rate: number;
  meetings: number;
}

export interface SequenceAnalytics {
  sequence_id: string;
  sequence_name: string;
  version_id: string;
  version: number;
  funnel: FunnelStep[];
  channels: ChannelComparisonRow[];
  totals: Record<string, number>;
}

export interface InboxHealthRow {
  id: string;
  email: string;
  display_name: string;
  status: InboxStatus;
  sends_today: number;
  daily_cap: number;
  bounce_rate: number;
  warmup_days: number | null;
  suspended: boolean;
  paused_reason: string | null;
}

export interface FailureRow {
  reason: string;
  count: number;
  channel: string;
}

export interface NeedsAttentionRow {
  kind: "reply" | "task" | "stalled";
  at: string;
  contact_id: string;
  contact_label: string;
  summary: string;
  sentiment: string;
  severity: "info" | "warn" | "danger";
}

export interface VersionComparisonRow {
  version: number;
  version_id: string;
  sent: number;
  replies: number;
  reply_rate: number;
  meetings: number;
  positive_replies: number;
}

export interface MetaInfo {
  app_env: string;
  channels: Record<string, string>;
  channel_mode: string;
  crm_mode: string;
  policy: {
    min_hours_between_touches: number;
    max_touches: number;
    max_sms: number;
    sms_quiet_hours: boolean;
    sms_window: [number, number];
    sms_allow_weekends: boolean;
  };
  reaction_policy: string;
  tracking: { open_pixel: boolean; link_rewriting: boolean; signals: string[] };
}
