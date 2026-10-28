import { useState } from "react";

import { useWorkspace } from "../app/WorkspaceContext";
import { api } from "../lib/api";
import { useInboxHealth, useInboxes } from "../lib/hooks";
import { pct } from "../lib/format";
import { Panel, PanelSkeleton } from "../components/ui/Panel";
import { Button } from "../components/ui/Button";
import { DataTable, KeyValue, type Column } from "../components/ui/Data";
import { Banner, ProgressBar, StatusPill, type Tone } from "../components/ui/Indicators";
import { EmptyState, ErrorState, Modal } from "../components/ui/Feedback";
import type { Inbox, InboxHealthRow } from "../lib/types";

const STATUS_TONE: Record<string, Tone> = {
  active: "accent",
  warming: "warm",
  paused: "danger",
};

export function InboxesPage() {
  const { clientId } = useWorkspace();
  const inboxes = useInboxes(clientId);
  const health = useInboxHealth(clientId);
  const [selected, setSelected] = useState<InboxHealthRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const healthById = new Map((health.data ?? []).map((row) => [row.id, row]));
  const activeCount = (health.data ?? []).filter((row) => row.status === "active").length;

  async function setStatus(inbox: Inbox, status: Inbox["status"]) {
    setBusy(true);
    setError(null);
    try {
      await api.updateInbox(inbox.id, { status });
      await Promise.all([inboxes.refetch(), health.refetch()]);
      setSelected(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the inbox.");
    } finally {
      setBusy(false);
    }
  }

  async function resetCounter(inbox: Inbox) {
    setBusy(true);
    setError(null);
    try {
      await api.resetInboxCounter(inbox.id);
      await health.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the counter.");
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<Inbox>[] = [
    {
      key: "email",
      header: "Inbox",
      render: (row) => (
        <span>
          <span className="block text-ink">{row.email}</span>
          <span className="block font-mono text-[11px] text-meta">{row.display_name}</span>
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (row) => (
        <StatusPill
          tone={STATUS_TONE[row.status] ?? "muted"}
          label={row.status}
          glow={row.status === "active"}
        />
      ),
    },
    {
      key: "today",
      header: "Today / cap",
      render: (row) => {
        const rowHealth = healthById.get(row.id);
        const sent = rowHealth?.sends_today ?? 0;
        return (
          <div className="flex min-w-32 items-center gap-2">
            <ProgressBar value={sent} max={row.daily_cap} tone={sent >= row.daily_cap ? "warm" : "accent"} />
            <span className="tabular whitespace-nowrap font-mono text-[11px] text-meta">
              {sent}/{row.daily_cap}
            </span>
          </div>
        );
      },
    },
    {
      key: "bounce",
      header: "Bounce rate",
      align: "right",
      render: (row) => {
        const value = healthById.get(row.id)?.bounce_rate ?? 0;
        return <span className={value > 3 ? "text-danger" : "text-body-2"}>{pct(value)}</span>;
      },
    },
    {
      key: "warmup",
      header: "Warm-up",
      align: "right",
      render: (row) => {
        const days = healthById.get(row.id)?.warmup_days;
        return days === null || days === undefined ? "—" : `${days}d`;
      },
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (row) => (
        <div className="flex justify-end gap-2">
          <Button
            size="sm"
            disabled={busy}
            onClick={() => setStatus(row, row.status === "active" ? "paused" : "active")}
          >
            {row.status === "active" ? "Pause" : "Activate"}
          </Button>
          <Button size="sm" onClick={() => resetCounter(row)} disabled={busy}>
            Reset counter
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-[12px] text-body-2">
          Email sends rotate across this pool. Bounce thresholds auto-pause an inbox (§8.9): retry
          transient errors, classify bounces, never reuse a burned address.
        </p>
        <StatusPill tone={activeCount > 0 ? "accent" : "danger"} label={`${activeCount} active`} glow={activeCount > 0} />
      </div>

      {(inboxes.error || health.error) && (
        <ErrorState
          message={((inboxes.error ?? health.error) as Error).message}
          onRetry={() => {
            void inboxes.refetch();
            void health.refetch();
          }}
        />
      )}

      {error && <ErrorState message={error} onRetry={() => setError(null)} />}

      {activeCount === 0 && (inboxes.data ?? []).length > 0 && (
        <Banner tone="danger" title="The pool has no active inboxes">
          The engine halts the client when it cannot send. Activate a warmed inbox before re-enabling
          the workspace.
        </Banner>
      )}

      <Panel kicker="Sending pool" title="Inboxes" bodyClassName="px-0 py-0">
        {inboxes.isLoading ? (
          <div className="p-5">
            <PanelSkeleton rows={5} />
          </div>
        ) : (inboxes.data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="No sending inboxes"
              description="Email cannot send until at least one inbox exists and is marked active."
            />
          </div>
        ) : (
          <DataTable
            columns={columns}
            rows={inboxes.data ?? []}
            rowKey={(row) => row.id}
            onRowClick={(row) => setSelected(healthById.get(row.id) ?? null)}
          />
        )}
      </Panel>

      <Panel kicker="Warm-up" title="Pool policy" className="max-w-2xl">
        <KeyValue label="Rotation" value="least-sent inbox under its daily cap" />
        <KeyValue label="Auto-pause" value="bounce rate or repeated transient failures" />
        <KeyValue label="Halt when empty" value="yes — the client kill switch trips automatically" />
        <KeyValue label="Tracking" value="reply-only (no pixels, no link rewriting)" />
      </Panel>

      <Modal
        open={Boolean(selected)}
        title={selected?.email ?? "Inbox"}
        onClose={() => setSelected(null)}
        footer={<Button onClick={() => setSelected(null)}>Close</Button>}
      >
        {selected && (
          <div>
            <KeyValue label="Status" value={selected.status} />
            <KeyValue label="Sends today" value={`${selected.sends_today} / ${selected.daily_cap}`} />
            <KeyValue label="Bounce rate" value={pct(selected.bounce_rate)} />
            <KeyValue label="Warm-up" value={selected.warmup_days === null ? "—" : `${selected.warmup_days} days`} />
            <KeyValue label="Suspended by engine" value={selected.suspended ? "yes" : "no"} />
            {selected.paused_reason && <KeyValue label="Reason" value={selected.paused_reason} />}
          </div>
        )}
      </Modal>
    </div>
  );
}
