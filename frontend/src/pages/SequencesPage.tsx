import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import { api } from "../lib/api";
import { useEnrollments, useSequences } from "../lib/hooks";
import { dateTime, num } from "../lib/format";
import { Button } from "../components/ui/Button";
import { Panel, PanelSkeleton } from "../components/ui/Panel";
import { DataTable, type Column } from "../components/ui/Data";
import { StatusPill } from "../components/ui/Indicators";
import { EmptyState, ErrorState, Modal } from "../components/ui/Feedback";
import { Field, Input, Textarea } from "../components/ui/Fields";
import { emptyGraph } from "../features/canvas/layout";
import type { Sequence } from "../lib/types";

export function SequencesPage() {
  const { clientId } = useWorkspace();
  const navigate = useNavigate();
  const sequences = useSequences(clientId);
  const enrollments = useEnrollments(clientId, { status: "active" });

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeBySequence = (enrollments.data ?? []).reduce<Record<string, number>>((acc, row) => {
    acc[row.sequence_id] = (acc[row.sequence_id] ?? 0) + 1;
    return acc;
  }, {});

  async function create() {
    if (!clientId || !name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.createSequence(clientId, {
        name: name.trim(),
        description: description.trim(),
        graph: emptyGraph(),
      });
      setCreating(false);
      navigate(`/sequences/${created.sequence.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the sequence.");
    } finally {
      setSubmitting(false);
    }
  }

  const columns: Column<Sequence>[] = [
    {
      key: "name",
      header: "Sequence",
      render: (row) => (
        <span>
          <span className="block text-ink">{row.name}</span>
          <span className="block font-mono text-[11px] text-meta">{row.description || "—"}</span>
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (row) => (
        <StatusPill
          tone={row.status === "active" ? "accent" : row.status === "paused" ? "danger" : "muted"}
          label={row.status}
          glow={row.status === "active"}
        />
      ),
    },
    {
      key: "active",
      header: "Active contacts",
      align: "right",
      render: (row) => num(activeBySequence[row.id] ?? 0),
    },
    {
      key: "version",
      header: "Version",
      align: "right",
      render: (row) => (row.current_version_id ? "published" : "—"),
    },
    { key: "updated", header: "Updated", align: "right", render: (row) => dateTime(row.updated_at) },
  ];

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-[12px] text-body-2">
          One active sequence per contact per channel. Reactions cancel the pending steps and
          re-branch immediately.
        </p>
        <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
          New sequence
        </Button>
      </div>

      {sequences.error && (
        <ErrorState message={(sequences.error as Error).message} onRetry={() => sequences.refetch()} />
      )}

      <Panel kicker="Sequences" title="All sequences" bodyClassName="px-0 py-0">
        {sequences.isLoading ? (
          <div className="p-5">
            <PanelSkeleton rows={5} />
          </div>
        ) : (sequences.data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="No sequences yet"
              description="Create one and the builder opens with a single email step."
              action={
                <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
                  New sequence
                </Button>
              }
            />
          </div>
        ) : (
          <DataTable
            columns={columns}
            rows={sequences.data ?? []}
            rowKey={(row) => row.id}
            onRowClick={(row) => navigate(`/sequences/${row.id}`)}
          />
        )}
      </Panel>

      <Modal
        open={creating}
        title="New sequence"
        onClose={() => setCreating(false)}
        footer={
          <>
            <Button onClick={() => setCreating(false)}>Cancel</Button>
            <Button variant="primary" onClick={create} loading={submitting}>
              Create
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <Field label="Name" htmlFor="sequence-name" error={error}>
            <Input
              id="sequence-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Acme — Hiring-Signal Nurture"
            />
          </Field>
          <Field label="Description" htmlFor="sequence-description">
            <Textarea
              id="sequence-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="6 touches over 14 days: 4 email + 2 SMS, branching on reply and meetings."
            />
          </Field>
        </div>
      </Modal>
    </div>
  );
}
