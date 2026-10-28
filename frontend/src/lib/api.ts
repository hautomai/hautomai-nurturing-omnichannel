/**
 * A tiny fetch wrapper. No axios, no generated client.
 *
 * The bearer token lives in `localStorage` under one key and is injected here,
 * so no component ever builds an Authorization header. `ApiError` carries the
 * HTTP status so callers can branch on 401 (re-login) vs 409 (already enrolled)
 * without parsing strings.
 */

import type {
  Client,
  Contact,
  Enrollment,
  FailureRow,
  ImportResult,
  Inbox,
  InboxHealthRow,
  MetaInfo,
  NeedsAttentionRow,
  OverviewMetrics,
  Persona,
  PhoneNumber,
  PublishResult,
  Sequence,
  SequenceAnalytics,
  SequenceDetail,
  SequenceVersion,
  SequenceGraph,
  SimulatedStep,
  Template,
  TimelineEntry,
  User,
  ValidateResult,
  VersionComparisonRow,
} from "./types";

const TOKEN_KEY = "hautomai.token";

/** Fields the CSV importer can map a column onto. */
export type ImportField =
  | "email"
  | "phone_e164"
  | "first_name"
  | "last_name"
  | "company"
  | "job_title"
  | "timezone";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type QueryValue = string | number | boolean | null | undefined;

function withQuery(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function request<T>(
  method: string,
  path: string,
  options: { body?: unknown; query?: Record<string, QueryValue>; form?: FormData } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form; // Let the browser set the multipart boundary.
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(withQuery(path, options.query), {
    method,
    headers,
    body,
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? (payload as { detail: unknown }).detail
        : payload;
    throw new ApiError(response.status, messageFrom(detail) ?? response.statusText, detail);
  }

  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function messageFrom(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string } | undefined;
    if (first?.msg) return first.msg;
  }
  return null;
}

export const api = {
  /* --- meta & auth ---------------------------------------------------- */
  meta: () => request<MetaInfo>("GET", "/api/meta"),
  login: (email: string, password: string) =>
    request<{ access_token: string; token_type: string; expires_in: number }>(
      "POST",
      "/api/auth/login",
      { body: { email, password } },
    ),
  me: () => request<User>("GET", "/api/auth/me"),

  /* --- clients & sending assets --------------------------------------- */
  clients: () => request<Client[]>("GET", "/api/clients"),
  client: (id: string) => request<Client>("GET", `/api/clients/${id}`),
  updateClient: (id: string, body: Partial<Pick<Client, "sending_halted" | "halt_reason" | "default_timezone">>) =>
    request<Client>("PATCH", `/api/clients/${id}`, { body }),

  personas: (clientId: string) => request<Persona[]>("GET", `/api/clients/${clientId}/personas`),

  inboxes: (clientId: string) => request<Inbox[]>("GET", `/api/clients/${clientId}/inboxes`),
  updateInbox: (id: string, body: Partial<Pick<Inbox, "status" | "daily_cap">>) =>
    request<Inbox>("PATCH", `/api/inboxes/${id}`, { body }),
  resetInboxCounter: (id: string) => request<Inbox>("POST", `/api/inboxes/${id}/reset-counter`),

  phoneNumbers: (clientId: string) => request<PhoneNumber[]>("GET", `/api/clients/${clientId}/phone-numbers`),

  /* --- templates ------------------------------------------------------- */
  templates: (clientId: string) => request<Template[]>("GET", `/api/clients/${clientId}/templates`),
  createTemplate: (clientId: string, body: Omit<Template, "id" | "client_id">) =>
    request<Template>("POST", `/api/clients/${clientId}/templates`, { body }),
  updateTemplate: (id: string, body: Partial<Omit<Template, "id" | "client_id" | "key" | "channel">>) =>
    request<Template>("PATCH", `/api/templates/${id}`, { body }),
  deleteTemplate: (id: string) => request<void>("DELETE", `/api/templates/${id}`),

  /* --- contacts -------------------------------------------------------- */
  contacts: (clientId: string, query?: { search?: string; tag?: string; limit?: number }) =>
    request<Contact[]>("GET", `/api/clients/${clientId}/contacts`, { query }),
  contact: (id: string) => request<Contact>("GET", `/api/contacts/${id}`),
  updateContact: (id: string, body: Partial<Contact>) =>
    request<Contact>("PATCH", `/api/contacts/${id}`, { body }),
  deleteContact: (id: string) => request<void>("DELETE", `/api/contacts/${id}`),
  contactEnrollments: (id: string) => request<Enrollment[]>("GET", `/api/contacts/${id}/enrollments`),
  contactTimeline: (id: string, limit = 200) =>
    request<TimelineEntry[]>("GET", `/api/contacts/${id}/timeline`, { query: { limit } }),
  /**
   * The backend takes per-field column names as query parameters, e.g.
   * `email_column=Work Email`; `mapping` is keyed by contact field.
   */
  importContacts: (
    clientId: string,
    file: File,
    mapping: Partial<Record<ImportField, string>>,
    options: { enroll_sequence_id?: string; dry_run?: boolean } = {},
  ) => {
    const form = new FormData();
    form.append("file", file);
    const query: Record<string, QueryValue> = {};
    for (const [field, column] of Object.entries(mapping)) {
      if (column) query[`${field}_column`] = column;
    }
    if (options.enroll_sequence_id) query.enroll_sequence_id = options.enroll_sequence_id;
    if (options.dry_run) query.dry_run = true;
    return request<ImportResult>("POST", `/api/clients/${clientId}/contacts/import`, { form, query });
  },

  /* --- sequences ------------------------------------------------------- */
  sequences: (clientId: string) => request<Sequence[]>("GET", `/api/clients/${clientId}/sequences`),
  createSequence: (clientId: string, body: { name: string; description?: string; graph?: SequenceGraph }) =>
    request<SequenceDetail>("POST", `/api/clients/${clientId}/sequences`, { body }),
  sequence: (id: string) => request<SequenceDetail>("GET", `/api/sequences/${id}`),
  updateSequence: (id: string, body: Partial<Sequence>) =>
    request<Sequence>("PATCH", `/api/sequences/${id}`, { body }),
  versions: (id: string) => request<SequenceVersion[]>("GET", `/api/sequences/${id}/versions`),
  saveDraft: (id: string, graph: SequenceGraph, notes = "") =>
    request<SequenceVersion>("PUT", `/api/sequences/${id}/draft`, { body: { graph, notes } }),
  validate: (id: string, graph: SequenceGraph) =>
    request<ValidateResult>("POST", `/api/sequences/${id}/validate`, { body: { graph } }),
  publish: (id: string) => request<PublishResult>("POST", `/api/sequences/${id}/publish`),
  rollback: (id: string, version: number) =>
    request<PublishResult>("POST", `/api/sequences/${id}/rollback/${version}`),
  simulate: (
    id: string,
    contactId: string,
    hypotheticals: Record<string, boolean> = {},
    maxSteps = 40,
  ) =>
    request<{ timeline: SimulatedStep[]; exited: boolean; exit_reason: string | null; warnings: string[] }>(
      "POST",
      `/api/sequences/${id}/simulate`,
      { body: { contact_id: contactId, hypotheticals, max_steps: maxSteps } },
    ),

  /* --- enrollments ----------------------------------------------------- */
  enrollments: (clientId: string, query?: { status?: string; sequence_id?: string }) =>
    request<Enrollment[]>("GET", `/api/clients/${clientId}/enrollments`, { query }),
  enroll: (contactId: string, sequenceId: string) =>
    request<Enrollment>("POST", "/api/enrollments", {
      body: { contact_id: contactId, sequence_id: sequenceId },
    }),
  bulkEnroll: (sequenceId: string, body: { contact_ids?: string[]; tag?: string }) =>
    request<{ enrolled: number; rejected: { reason: string }[] }>(
      "POST",
      `/api/sequences/${sequenceId}/enroll`,
      { body },
    ),
  pauseEnrollment: (id: string, reason = "") =>
    request<Enrollment>("POST", `/api/enrollments/${id}/pause`, { body: { reason } }),
  resumeEnrollment: (id: string) => request<Enrollment>("POST", `/api/enrollments/${id}/resume`),
  exitEnrollment: (id: string, reason = "") =>
    request<Enrollment>("POST", `/api/enrollments/${id}/exit`, { body: { reason } }),
  sendNext: (id: string) => request<Enrollment>("POST", `/api/enrollments/${id}/send-next`),
  skipWait: (id: string) => request<Enrollment>("POST", `/api/enrollments/${id}/skip-wait`),

  /* --- analytics -------------------------------------------------------- */
  overview: (clientId: string, days: number) =>
    request<OverviewMetrics>("GET", `/api/clients/${clientId}/analytics/overview`, { query: { days } }),
  sequenceAnalytics: (sequenceId: string, query: { version_id?: string } = {}) =>
    request<SequenceAnalytics>("GET", `/api/sequences/${sequenceId}/analytics`, { query }),
  inboxHealth: (clientId: string) =>
    request<InboxHealthRow[]>("GET", `/api/clients/${clientId}/analytics/inboxes`),
  failures: (clientId: string, days: number) =>
    request<FailureRow[]>("GET", `/api/clients/${clientId}/analytics/failures`, { query: { days } }),
  needsAttention: (clientId: string, limit = 50) =>
    request<NeedsAttentionRow[]>("GET", `/api/clients/${clientId}/analytics/needs-attention`, {
      query: { limit },
    }),
  versionCompare: (sequenceId: string, limit = 2) =>
    request<VersionComparisonRow[]>("GET", `/api/sequences/${sequenceId}/analytics/version-compare`, {
      query: { limit },
    }),
};
