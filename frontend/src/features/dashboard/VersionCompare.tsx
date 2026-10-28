import { useState } from "react";

import { Panel, PanelSkeleton } from "../../components/ui/Panel";
import { BulletList, DataTable, type Column } from "../../components/ui/Data";
import { Button } from "../../components/ui/Button";
import { num, pct } from "../../lib/format";
import type { VersionComparisonRow } from "../../lib/types";

/**
 * Row 5, full width, collapsed by default (§22.2). This is the payoff of
 * immutable versions (§8.6) and the reason the panel exists even though A/B
 * testing itself is out of scope.
 */
export function VersionCompare({
  rows,
  loading,
  sequenceName,
}: {
  rows: VersionComparisonRow[];
  loading: boolean;
  sequenceName?: string;
}) {
  const [open, setOpen] = useState(false);

  const columns: Column<VersionComparisonRow>[] = [
    { key: "version", header: "Version", render: (row) => `v${row.version}` },
    { key: "sent", header: "Sent", align: "right", render: (row) => num(row.sent) },
    { key: "replies", header: "Replies", align: "right", render: (row) => num(row.replies) },
    { key: "rate", header: "Reply rate", align: "right", render: (row) => pct(row.reply_rate) },
    {
      key: "positive",
      header: "Positive",
      align: "right",
      render: (row) => num(row.positive_replies),
    },
    { key: "meetings", header: "Meetings", align: "right", render: (row) => num(row.meetings) },
  ];

  const [latest, previous] = rows;

  return (
    <Panel
      kicker="Version attribution"
      title={sequenceName ? `${sequenceName} · last two versions` : "Version compare"}
      actions={
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
        >
          {open ? "Hide" : "Show"}
        </Button>
      }
      footer={["Immutable versions", rows.length > 0 ? `v${rows[0].version} → v${rows[rows.length - 1].version}` : ""]}
    >
      {!open ? (
        <p className="font-mono text-[12.5px] text-body-2">
          Collapsed by default — expand to attribute replies and meetings to a version.
        </p>
      ) : loading ? (
        <PanelSkeleton rows={3} />
      ) : rows.length === 0 ? (
        <p className="font-mono text-[12.5px] text-body-2">
          Only one version has been published, so there is nothing to compare yet.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          <DataTable columns={columns} rows={rows} rowKey={(row) => row.version_id} dense />
          <BulletList
            items={[
              latest && previous
                ? {
                    text: compare(latest, previous),
                    tone:
                      latest.reply_rate >= previous.reply_rate
                        ? ("accent" as const)
                        : ("warm" as const),
                  }
                : { text: "Publish a second version to see a comparison.", tone: "muted" as const },
            ]}
          />
        </div>
      )}
    </Panel>
  );
}

function compare(latest: VersionComparisonRow, previous: VersionComparisonRow): string {
  const delta = latest.reply_rate - previous.reply_rate;
  const direction = delta >= 0 ? "up" : "down";
  return `v${latest.version} is ${direction} ${Math.abs(delta).toFixed(1)} pts on reply rate vs v${previous.version} (${pct(
    latest.reply_rate,
  )} vs ${pct(previous.reply_rate)}).`;
}
