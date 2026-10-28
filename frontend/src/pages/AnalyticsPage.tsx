import { useMemo, useState } from "react";

import { useWorkspace } from "../app/WorkspaceContext";
import { KpiStrip } from "../features/dashboard/KpiStrip";
import { SequenceFunnel } from "../features/dashboard/SequenceFunnel";
import { ChannelComparison } from "../features/dashboard/ChannelComparison";
import { SendingHealth } from "../features/dashboard/SendingHealth";
import { FailureReport } from "../features/dashboard/FailureReport";
import { Panel } from "../components/ui/Panel";
import { Button, SegmentedControl } from "../components/ui/Button";
import { DataTable, type Column } from "../components/ui/Data";
import { StatusPill } from "../components/ui/Indicators";
import { EmptyState } from "../components/ui/Feedback";
import {
  useFailures,
  useInboxHealth,
  useOverview,
  useSequence,
  useSequenceAnalytics,
  useSequences,
  useVersionCompare,
} from "../lib/hooks";
import { num, pct } from "../lib/format";
import type { VersionComparisonRow } from "../lib/types";

const RANGES = [
  { value: "7d", label: "7d", days: 7 },
  { value: "30d", label: "30d", days: 30 },
  { value: "90d", label: "90d", days: 90 },
];

/**
 * The same rollups as the dashboard, focused on one sequence and with the
 * version history uncollapsed — the deeper analysis view (§13).
 */
export function AnalyticsPage() {
  const { clientId } = useWorkspace();
  const [range, setRange] = useState("30d");
  const [sequenceId, setSequenceId] = useState<string | null>(null);
  const [versionId, setVersionId] = useState<string | null>(null);

  const days = RANGES.find((item) => item.value === range)?.days ?? 30;
  const sequences = useSequences(clientId);
  const rows = useMemo(() => sequences.data ?? [], [sequences.data]);
  const activeSequenceId = sequenceId ?? rows.find((row) => row.current_version_id)?.id ?? rows[0]?.id ?? null;

  const detail = useSequence(activeSequenceId);
  const publishedVersions = (detail.data?.versions ?? []).filter((version) => version.published_at);
  const activeVersionId = versionId ?? detail.data?.sequence.current_version_id ?? null;

  const overview = useOverview(clientId, days);
  const analytics = useSequenceAnalytics(activeSequenceId, activeVersionId);
  const inboxHealth = useInboxHealth(clientId);
  const failures = useFailures(clientId, days);
  const compare = useVersionCompare(activeSequenceId);

  const compareColumns: Column<VersionComparisonRow>[] = [
    { key: "version", header: "Version", render: (row) => `v${row.version}` },
    { key: "sent", header: "Sent", align: "right", render: (row) => num(row.sent) },
    { key: "replies", header: "Replies", align: "right", render: (row) => num(row.replies) },
    { key: "rate", header: "Reply rate", align: "right", render: (row) => pct(row.reply_rate) },
    { key: "positive", header: "Positive", align: "right", render: (row) => num(row.positive_replies) },
    { key: "meetings", header: "Meetings", align: "right", render: (row) => num(row.meetings) },
  ];

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SegmentedControl
          ariaLabel="Date range"
          value={range}
          options={RANGES.map(({ value, label }) => ({ value, label }))}
          onChange={setRange}
        />
        <div className="flex flex-wrap items-center gap-2">
          {rows.map((sequence) => (
            <Button
              key={sequence.id}
              size="sm"
              variant={sequence.id === activeSequenceId ? "primary" : "neutral"}
              onClick={() => {
                setSequenceId(sequence.id);
                setVersionId(null);
              }}
            >
              {sequence.name}
            </Button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <Panel title="Analytics">
          <EmptyState
            title="Nothing to analyse yet"
            description="Publish a sequence and enroll contacts — every panel reads from the rollups."
          />
        </Panel>
      ) : (
        <>
          <KpiStrip data={overview.data} loading={overview.isLoading} />

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <SequenceFunnel
                analytics={analytics.data}
                versions={publishedVersions}
                loading={analytics.isLoading || detail.isLoading}
                selectedVersionId={activeVersionId}
                onVersionChange={setVersionId}
                onStepClick={() => undefined}
                selectedStepKey={null}
                drillHrefFor={(step) =>
                  `/contacts?sequence=${activeSequenceId}&step=${encodeURIComponent(step.step_key)}`
                }
              />
            </div>
            <ChannelComparison rows={analytics.data?.channels ?? []} loading={analytics.isLoading} />
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <SendingHealth
              rows={inboxHealth.data ?? []}
              loading={inboxHealth.isLoading}
              onDrill={() => undefined}
            />
            <FailureReport rows={failures.data ?? []} loading={failures.isLoading} />
          </div>

          <Panel
            kicker="Attribution"
            title="Reply and meeting attribution by version"
            footer={["Immutable versions", `${(compare.data ?? []).length} compared`]}
          >
            <DataTable
              columns={compareColumns}
              rows={compare.data ?? []}
              rowKey={(row) => row.version_id}
              dense
              empty={<p className="font-mono text-[12.5px] text-body-2">Publish a second version to compare.</p>}
            />
            <div className="mt-3 flex flex-wrap gap-2">
              <StatusPill tone="accent" label="Reply-only attribution" />
              <StatusPill tone="muted" label="No open or click tracking in v1" />
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}
