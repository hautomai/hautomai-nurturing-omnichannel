import { useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import { KpiStrip } from "../features/dashboard/KpiStrip";
import { SequenceFunnel } from "../features/dashboard/SequenceFunnel";
import { ChannelComparison } from "../features/dashboard/ChannelComparison";
import { SendingHealth } from "../features/dashboard/SendingHealth";
import { FailureReport } from "../features/dashboard/FailureReport";
import { NeedsAttention } from "../features/dashboard/NeedsAttention";
import { VersionCompare } from "../features/dashboard/VersionCompare";
import { Panel, PanelSkeleton } from "../components/ui/Panel";
import { EmptyState, ErrorState } from "../components/ui/Feedback";
import { Button, SegmentedControl } from "../components/ui/Button";
import { NumberedStep } from "../components/ui/Data";
import {
  useFailures,
  useInboxHealth,
  useNeedsAttention,
  useOverview,
  useSequence,
  useSequenceAnalytics,
  useSequences,
  useVersionCompare,
} from "../lib/hooks";
import type { FunnelStep } from "../lib/types";

const RANGES = [
  { value: "7d", label: "7d", days: 7 },
  { value: "30d", label: "30d", days: 30 },
  { value: "90d", label: "90d", days: 90 },
];

/**
 * The app home (§22). Everything derives from the URL — `?sequence=&version=&range=`
 * — so a drilled-through view is shareable, and the funnel, channel comparison,
 * health and failure panels all speak about the same selected sequence version.
 */
export function DashboardPage() {
  const { clientId } = useWorkspace();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const range = params.get("range") ?? "30d";
  const days = RANGES.find((item) => item.value === range)?.days ?? 30;
  const sequenceParam = params.get("sequence");
  const versionParam = params.get("version");
  const stepParam = params.get("step");

  const sequencesQuery = useSequences(clientId);
  const sequences = useMemo(() => sequencesQuery.data ?? [], [sequencesQuery.data]);

  // Default to the first sequence with a published version, else the first one.
  const selectedSequenceId =
    sequenceParam ?? sequences.find((row) => row.current_version_id)?.id ?? sequences[0]?.id ?? null;

  const detailQuery = useSequence(selectedSequenceId);
  const publishedVersions = useMemo(
    () => (detailQuery.data?.versions ?? []).filter((version) => version.published_at),
    [detailQuery.data],
  );

  const selectedVersionId =
    versionParam ??
    detailQuery.data?.sequence.current_version_id ??
    publishedVersions[0]?.id ??
    null;

  const overview = useOverview(clientId, days);
  const analytics = useSequenceAnalytics(selectedSequenceId, selectedVersionId);
  const inboxHealth = useInboxHealth(clientId);
  const failures = useFailures(clientId, days);
  const needsAttention = useNeedsAttention(clientId);
  const versionCompare = useVersionCompare(selectedSequenceId);

  const patchParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace: true });
  };

  const drillHrefFor = (step: FunnelStep) => {
    const next = new URLSearchParams(params);
    next.set("step", step.step_key);
    return `/contacts?${next.toString()}`;
  };

  const hasSequences = sequences.length > 0;
  const anyError =
    overview.error ?? detailQuery.error ?? analytics.error ?? inboxHealth.error ?? failures.error;

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <SegmentedControl
            ariaLabel="Date range"
            value={range}
            options={RANGES.map(({ value, label }) => ({ value, label }))}
            onChange={(value) => patchParams({ range: value })}
          />
          <span className="font-mono text-[11px] text-meta-2">
            Campaign performance · {analytics.data?.sequence_name ?? "no sequence selected"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={() => {
              void overview.refetch();
              void analytics.refetch();
              void inboxHealth.refetch();
              void failures.refetch();
              void needsAttention.refetch();
            }}
            loading={overview.isFetching && !overview.isLoading}
          >
            Refresh
          </Button>
        </div>
      </div>

      {anyError && (
        <ErrorState
          message={(anyError as Error).message}
          onRetry={() => {
            void overview.refetch();
            void analytics.refetch();
            void inboxHealth.refetch();
          }}
        />
      )}

      {!sequencesQuery.isLoading && !hasSequences ? (
        <Panel kicker="First run" title="Get the workspace moving">
          <EmptyState
            title="No sequences yet"
            description="Three steps and the dashboard has real shape."
            steps={[
              {
                title: "Import contacts",
                body: "CSV upload from your existing lists, or add them one at a time.",
                onClick: () => navigate("/contacts"),
              },
              {
                title: "Connect a sending inbox",
                body: "Rotated SMTP inboxes for email, a Twilio number for SMS.",
                onClick: () => navigate("/inboxes"),
              },
              {
                title: "Publish a sequence",
                body: "Build the graph, validate it, publish a first immutable version.",
                onClick: () => navigate("/sequences"),
              },
            ]}
            action={
              <div className="rounded-card border border-line px-3">
                <NumberedStep index={1} title="Seed data" >
                  <code className="text-body-2">SEED_ON_STARTUP=true</code> loads the Acme
                  sequence, templates and the 4-inbox pool.
                </NumberedStep>
              </div>
            }
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
                loading={analytics.isLoading || detailQuery.isLoading}
                selectedVersionId={selectedVersionId}
                onVersionChange={(versionId) => patchParams({ version: versionId, step: null })}
                onStepClick={(step) => {
                  patchParams({ step: step.step_key });
                  navigate(drillHrefFor(step));
                }}
                selectedStepKey={stepParam}
                drillHrefFor={drillHrefFor}
              />
            </div>
            <ChannelComparison
              rows={analytics.data?.channels ?? []}
              loading={analytics.isLoading}
            />
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <SendingHealth
              rows={inboxHealth.data ?? []}
              loading={inboxHealth.isLoading}
              onDrill={() => navigate(`/inboxes?range=${range}`)}
            />
            <FailureReport rows={failures.data ?? []} loading={failures.isLoading} />
          </div>

          <NeedsAttention rows={needsAttention.data ?? []} loading={needsAttention.isLoading} />

          <VersionCompare
            rows={versionCompare.data ?? []}
            loading={versionCompare.isLoading}
            sequenceName={analytics.data?.sequence_name}
          />

          {sequences.length > 1 && (
            <Panel kicker="Sequence" title="Switch the funnel">
              {sequencesQuery.isLoading ? (
                <PanelSkeleton rows={3} />
              ) : (
                <div className="flex flex-wrap gap-2">
                  {sequences.map((sequence) => (
                    <Button
                      key={sequence.id}
                      size="sm"
                      variant={sequence.id === selectedSequenceId ? "primary" : "neutral"}
                      onClick={() => patchParams({ sequence: sequence.id, version: null, step: null })}
                    >
                      {sequence.name}
                    </Button>
                  ))}
                </div>
              )}
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
