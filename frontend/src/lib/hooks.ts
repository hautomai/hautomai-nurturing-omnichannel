/**
 * React Query hooks over the API client.
 *
 * Query keys are centralised so a mutation can invalidate exactly what changed
 * (e.g. publishing a sequence refreshes the canvas, the sequence list and the
 * dashboard rollups) instead of nuking the whole cache.
 */

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api } from "./api";
import type { SequenceGraph } from "./types";

export const keys = {
  meta: ["meta"] as const,
  clients: ["clients"] as const,
  client: (id: string) => ["client", id] as const,
  personas: (clientId: string) => ["personas", clientId] as const,
  inboxes: (clientId: string) => ["inboxes", clientId] as const,
  phoneNumbers: (clientId: string) => ["phone-numbers", clientId] as const,
  templates: (clientId: string) => ["templates", clientId] as const,
  contacts: (clientId: string, filters: Record<string, unknown>) => ["contacts", clientId, filters] as const,
  contact: (id: string) => ["contact", id] as const,
  timeline: (id: string) => ["timeline", id] as const,
  contactEnrollments: (id: string) => ["contact-enrollments", id] as const,
  sequences: (clientId: string) => ["sequences", clientId] as const,
  sequence: (id: string) => ["sequence", id] as const,
  versions: (id: string) => ["versions", id] as const,
  enrollments: (clientId: string, filters: Record<string, unknown>) =>
    ["enrollments", clientId, filters] as const,
  overview: (clientId: string, days: number) => ["overview", clientId, days] as const,
  sequenceAnalytics: (sequenceId: string, versionId: string | null) =>
    ["sequence-analytics", sequenceId, versionId] as const,
  inboxHealth: (clientId: string) => ["inbox-health", clientId] as const,
  failures: (clientId: string, days: number) => ["failures", clientId, days] as const,
  needsAttention: (clientId: string) => ["needs-attention", clientId] as const,
  versionCompare: (sequenceId: string) => ["version-compare", sequenceId] as const,
};

/** The dashboard lives on rollups, so it refetches on a slow tick (§22.3). */
const ROLLUP_REFRESH_MS = 60_000;

export function useMeta() {
  return useQuery({ queryKey: keys.meta, queryFn: api.meta, staleTime: 5 * 60_000 });
}

export function useClients() {
  return useQuery({ queryKey: keys.clients, queryFn: api.clients, staleTime: 5 * 60_000 });
}

export function useClient(clientId: string | null) {
  return useQuery({
    queryKey: keys.client(clientId ?? ""),
    queryFn: () => api.client(clientId as string),
    enabled: Boolean(clientId),
  });
}

export function useInboxes(clientId: string | null) {
  return useQuery({
    queryKey: keys.inboxes(clientId ?? ""),
    queryFn: () => api.inboxes(clientId as string),
    enabled: Boolean(clientId),
  });
}

export function useTemplates(clientId: string | null) {
  return useQuery({
    queryKey: keys.templates(clientId ?? ""),
    queryFn: () => api.templates(clientId as string),
    enabled: Boolean(clientId),
  });
}

export function useContacts(
  clientId: string | null,
  filters: { search?: string; tag?: string; limit?: number } = {},
) {
  return useQuery({
    queryKey: keys.contacts(clientId ?? "", filters),
    queryFn: () => api.contacts(clientId as string, filters),
    enabled: Boolean(clientId),
  });
}

export function useContact(contactId: string | null) {
  return useQuery({
    queryKey: keys.contact(contactId ?? ""),
    queryFn: () => api.contact(contactId as string),
    enabled: Boolean(contactId),
  });
}

export function useTimeline(contactId: string | null) {
  return useQuery({
    queryKey: keys.timeline(contactId ?? ""),
    queryFn: () => api.contactTimeline(contactId as string),
    enabled: Boolean(contactId),
  });
}

export function useContactEnrollments(contactId: string | null) {
  return useQuery({
    queryKey: keys.contactEnrollments(contactId ?? ""),
    queryFn: () => api.contactEnrollments(contactId as string),
    enabled: Boolean(contactId),
  });
}

export function useSequences(clientId: string | null) {
  return useQuery({
    queryKey: keys.sequences(clientId ?? ""),
    queryFn: () => api.sequences(clientId as string),
    enabled: Boolean(clientId),
  });
}

export function useSequence(sequenceId: string | null) {
  return useQuery({
    queryKey: keys.sequence(sequenceId ?? ""),
    queryFn: () => api.sequence(sequenceId as string),
    enabled: Boolean(sequenceId),
  });
}

export function useEnrollments(
  clientId: string | null,
  filters: { status?: string; sequence_id?: string } = {},
) {
  return useQuery({
    queryKey: keys.enrollments(clientId ?? "", filters),
    queryFn: () => api.enrollments(clientId as string, filters),
    enabled: Boolean(clientId),
  });
}

/* -------------------------------------------------------------------------
 * Dashboard rollups
 * ---------------------------------------------------------------------- */

export function useOverview(clientId: string | null, days: number) {
  return useQuery({
    queryKey: keys.overview(clientId ?? "", days),
    queryFn: () => api.overview(clientId as string, days),
    enabled: Boolean(clientId),
    refetchInterval: ROLLUP_REFRESH_MS,
  });
}

export function useSequenceAnalytics(sequenceId: string | null, versionId: string | null) {
  return useQuery({
    queryKey: keys.sequenceAnalytics(sequenceId ?? "", versionId),
    queryFn: () => api.sequenceAnalytics(sequenceId as string, { version_id: versionId ?? undefined }),
    enabled: Boolean(sequenceId),
    refetchInterval: ROLLUP_REFRESH_MS,
  });
}

export function useInboxHealth(clientId: string | null) {
  return useQuery({
    queryKey: keys.inboxHealth(clientId ?? ""),
    queryFn: () => api.inboxHealth(clientId as string),
    enabled: Boolean(clientId),
    refetchInterval: ROLLUP_REFRESH_MS,
  });
}

export function useFailures(clientId: string | null, days: number) {
  return useQuery({
    queryKey: keys.failures(clientId ?? "", days),
    queryFn: () => api.failures(clientId as string, days),
    enabled: Boolean(clientId),
    refetchInterval: ROLLUP_REFRESH_MS,
  });
}

export function useNeedsAttention(clientId: string | null) {
  return useQuery({
    queryKey: keys.needsAttention(clientId ?? ""),
    queryFn: () => api.needsAttention(clientId as string),
    enabled: Boolean(clientId),
    refetchInterval: ROLLUP_REFRESH_MS,
  });
}

export function useVersionCompare(sequenceId: string | null) {
  return useQuery({
    queryKey: keys.versionCompare(sequenceId ?? ""),
    queryFn: () => api.versionCompare(sequenceId as string),
    enabled: Boolean(sequenceId),
    refetchInterval: ROLLUP_REFRESH_MS,
  });
}

/* -------------------------------------------------------------------------
 * Mutations
 * ---------------------------------------------------------------------- */

function invalidateDashboard(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ["overview"] });
  void queryClient.invalidateQueries({ queryKey: ["sequence-analytics"] });
  void queryClient.invalidateQueries({ queryKey: ["inbox-health"] });
  void queryClient.invalidateQueries({ queryKey: ["failures"] });
  void queryClient.invalidateQueries({ queryKey: ["needs-attention"] });
  void queryClient.invalidateQueries({ queryKey: ["version-compare"] });
}

export function useSaveDraft(sequenceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ graph, notes = "" }: { graph: SequenceGraph; notes?: string }) =>
      api.saveDraft(sequenceId, graph, notes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.sequence(sequenceId) });
      void queryClient.invalidateQueries({ queryKey: keys.versions(sequenceId) });
    },
  });
}

export function usePublishSequence(sequenceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.publish(sequenceId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.sequence(sequenceId) });
      void queryClient.invalidateQueries({ queryKey: keys.versions(sequenceId) });
      void queryClient.invalidateQueries({ queryKey: ["sequences"] });
      invalidateDashboard(queryClient);
    },
  });
}

export function useValidateGraph(sequenceId: string) {
  return useMutation({
    mutationFn: (graph: SequenceGraph) => api.validate(sequenceId, graph),
  });
}

export function useSetHalt(clientId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { sending_halted: boolean; halt_reason?: string }) =>
      api.updateClient(clientId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.client(clientId) });
      void queryClient.invalidateQueries({ queryKey: ["enrollments"] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["needs-attention"] });
    },
  });
}

export function useEnrollContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { contactId: string; sequenceId: string }) =>
      api.enroll(input.contactId, input.sequenceId),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: keys.contactEnrollments(variables.contactId) });
      void queryClient.invalidateQueries({ queryKey: keys.timeline(variables.contactId) });
      invalidateDashboard(queryClient);
    },
  });
}

export function useEnrollmentAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      action,
      enrollmentId,
      reason,
    }: {
      action: "pause" | "resume" | "exit" | "send-next" | "skip-wait";
      enrollmentId: string;
      reason?: string;
    }) => {
      switch (action) {
        case "pause":
          return api.pauseEnrollment(enrollmentId, reason ?? "");
        case "resume":
          return api.resumeEnrollment(enrollmentId);
        case "exit":
          return api.exitEnrollment(enrollmentId, reason ?? "");
        case "send-next":
          return api.sendNext(enrollmentId);
        case "skip-wait":
          return api.skipWait(enrollmentId);
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["enrollments"] });
      void queryClient.invalidateQueries({ queryKey: ["contact-enrollments"] });
      void queryClient.invalidateQueries({ queryKey: ["timeline"] });
      invalidateDashboard(queryClient);
    },
  });
}

export function useSaveTemplate(clientId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { name?: string; subject?: string; body?: string } }) =>
      api.updateTemplate(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.templates(clientId) }),
  });
}

export function useDeleteTemplate(clientId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteTemplate(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.templates(clientId) }),
  });
}

export function useImportContacts(clientId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      file,
      mapping,
      enrollSequenceId,
      dryRun,
    }: {
      file: File;
      mapping: Record<string, string>;
      enrollSequenceId?: string;
      dryRun?: boolean;
    }) =>
      api.importContacts(clientId, file, mapping, {
        enroll_sequence_id: enrollSequenceId,
        dry_run: dryRun,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["enrollments"] });
      invalidateDashboard(queryClient);
    },
  });
}
