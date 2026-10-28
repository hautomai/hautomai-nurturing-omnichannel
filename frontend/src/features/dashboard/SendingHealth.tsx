import { useMemo } from "react";

import { Panel, PanelSkeleton } from "../../components/ui/Panel";
import { DataTable, type Column } from "../../components/ui/Data";
import { Banner, ProgressBar, StatusPill, type Tone } from "../../components/ui/Indicators";
import { num, pct } from "../../lib/format";
import type { InboxHealthRow } from "../../lib/types";

const STATUS_TONE: Record<string, Tone> = {
  active: "accent",
  warming: "warm",
  paused: "danger",
};

/**
 * Row 3, half width (§22.2). Grouped by sending domain because deliverability is
 * a domain-level property, not a mailbox-level one. The warm/danger banner
 * appears when the pool has zero active inboxes or any inbox was auto-paused.
 */
export function SendingHealth({
  rows,
  loading,
  onDrill,
}: {
  rows: InboxHealthRow[];
  loading: boolean;
  onDrill: (inbox: InboxHealthRow) => void;
}) {
  const grouped = useMemo(() => groupByDomain(rows), [rows]);

  const active = rows.filter((row) => row.status === "active");
  const suspended = rows.filter((row) => row.suspended || row.status === "paused");
  const poolCap = rows.reduce((total, row) => total + row.daily_cap, 0);
  const poolSent = rows.reduce((total, row) => total + row.sends_today, 0);

  const columns: Column<InboxHealthRow>[] = [
    {
      key: "email",
      header: "Inbox",
      render: (row) => <span className="text-ink">{row.email}</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (row) => (
        <StatusPill tone={STATUS_TONE[row.status] ?? "muted"} label={row.status} glow={row.status === "active"} />
      ),
    },
    {
      key: "cap",
      header: "Today / cap",
      render: (row) => (
        <div className="flex min-w-32 items-center gap-2">
          <ProgressBar
            value={row.sends_today}
            max={row.daily_cap}
            tone={row.sends_today >= row.daily_cap ? "warm" : "accent"}
            className="min-w-16"
          />
          <span className="tabular whitespace-nowrap font-mono text-[11px] text-meta">
            {row.sends_today}/{row.daily_cap}
          </span>
        </div>
      ),
    },
    {
      key: "bounce",
      header: "Bounce",
      align: "right",
      render: (row) => (
        <span className={row.bounce_rate > 3 ? "text-danger" : "text-body-2"}>
          {pct(row.bounce_rate)}
        </span>
      ),
    },
    {
      key: "warmup",
      header: "Warm-up",
      align: "right",
      render: (row) => (row.warmup_days === null ? "—" : `${row.warmup_days}d`),
    },
  ];

  return (
    <Panel
      kicker="Sending health"
      title="Inbox pool"
      footer={[
        `${active.length} active · ${suspended.length} paused`,
        `${num(poolSent)}/${num(poolCap)} sends today`,
      ]}
    >
      {loading ? (
        <PanelSkeleton rows={5} />
      ) : (
        <div className="flex flex-col gap-4">
          {rows.length === 0 && (
            <p className="font-mono text-[12.5px] text-body-2">
              No sender inboxes configured for this client.
            </p>
          )}

          {active.length === 0 && rows.length > 0 && (
            <Banner tone="danger" title="Zero active inboxes — sending is stopped">
              The engine auto-halts the client when the pool empties (§8.9). Warm an inbox or lift
              the halt manually before re-enrolling contacts.
            </Banner>
          )}

          {active.length > 0 && suspended.length > 0 && (
            <Banner tone="warm" title={`${suspended.length} inbox(es) paused by the engine`}>
              Bounce thresholds tripped or the counter was reset. Check the reason on each row before
              reactivating.
            </Banner>
          )}

          {grouped.map(([domain, inboxes]) => (
            <div key={domain} className="flex flex-col gap-2">
              <p className="font-mono text-[11px] uppercase tracking-kicker text-meta">
                {domain} · {inboxes.length} inbox{inboxes.length === 1 ? "" : "es"}
              </p>
              <DataTable
                columns={columns}
                rows={inboxes}
                rowKey={(row) => row.id}
                dense
                onRowClick={onDrill}
              />
              {inboxes
                .filter((row) => row.suspended && row.paused_reason)
                .map((row) => (
                  <p key={row.id} className="font-mono text-[11px] text-danger">
                    {row.email}: {row.paused_reason}
                  </p>
                ))}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function groupByDomain(rows: InboxHealthRow[]): [string, InboxHealthRow[]][] {
  const map = new Map<string, InboxHealthRow[]>();
  for (const row of rows) {
    const domain = row.email.split("@")[1] ?? "unknown";
    map.set(domain, [...(map.get(domain) ?? []), row]);
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}