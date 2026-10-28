import clsx from "clsx";

import { Panel, PanelSkeleton } from "../../components/ui/Panel";
import { ChannelTag, DataTable, FlowArrow, type Column } from "../../components/ui/Data";
import { MeasureBar, TONE_BY_CHANNEL } from "../../components/ui/Indicators";
import { pct } from "../../lib/format";
import type { FunnelStep, SequenceAnalytics, SequenceVersion } from "../../lib/types";

/**
 * Row 2, two-thirds width (§22.2).
 *
 * Bar length is proportional to `sent`. Drop-off between consecutive steps is
 * the deck's `.flow-arrow` carrying a percentage lost. Clicking a step drills
 * through to the contact list filtered to contacts at or past that step, with
 * the filter token in the URL so the view is shareable.
 */
export function SequenceFunnel({
  analytics,
  versions,
  loading,
  selectedVersionId,
  onVersionChange,
  onStepClick,
  selectedStepKey,
  drillHrefFor,
}: {
  analytics?: SequenceAnalytics;
  versions: SequenceVersion[];
  loading: boolean;
  selectedVersionId: string | null;
  onVersionChange: (versionId: string) => void;
  onStepClick: (step: FunnelStep) => void;
  selectedStepKey: string | null;
  drillHrefFor: (step: FunnelStep) => string;
}) {
  const funnel = analytics?.funnel ?? [];
  const maxSent = Math.max(1, ...funnel.map((step) => step.sent));

  const columns: Column<FunnelStep>[] = [
    { key: "index", header: "#", render: (row) => row.index + 1, className: "w-8 text-meta" },
    { key: "channel", header: "Channel", render: (row) => <ChannelTag channel={row.channel} /> },
    {
      key: "name",
      header: "Step",
      render: (row) => <span className="text-ink">{row.name || row.step_key}</span>,
    },
    { key: "sent", header: "Sent", align: "right", render: (row) => row.sent },
    {
      key: "reply_rate",
      header: "Reply rate",
      align: "right",
      render: (row) => (row.sent ? pct((row.replied / row.sent) * 100) : "—"),
    },
    { key: "meetings", header: "Meetings", align: "right", render: (row) => row.meetings },
  ];

  return (
    <Panel
      kicker="Sequence funnel"
      title={analytics?.sequence_name ?? "Select a sequence"}
      actions={
        <>
          <select
            aria-label="Sequence version"
            value={selectedVersionId ?? ""}
            onChange={(event) => onVersionChange(event.target.value)}
            className="rounded-input border border-line bg-[var(--field-fill)] px-3 py-1.5 font-mono text-[12px] text-ink focus:border-accent focus:outline-none"
          >
            {versions.length === 0 && <option value="">No published version</option>}
            {versions.map((version) => (
              <option key={version.id} value={version.id}>
                v{version.version}
              </option>
            ))}
          </select>
          <span className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 font-mono text-[11px] text-ink">
              <span aria-hidden className="h-2 w-2 rounded-full bg-accent" /> Email
            </span>
            <span className="flex items-center gap-1.5 font-mono text-[11px] text-warm">
              <span aria-hidden className="h-2 w-2 rounded-full bg-warm" /> SMS
            </span>
          </span>
        </>
      }
      footer={[
        "Bar length ∝ sent",
        analytics ? `v${analytics.version} · ${funnel.length} steps` : "",
      ]}
    >
      {loading ? (
        <PanelSkeleton rows={6} />
      ) : funnel.length === 0 ? (
        <p className="font-mono text-[12.5px] text-body-2">
          No sends recorded for this version yet. Enroll a contact or run a simulation from the
          builder.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          <ol className="flex flex-col gap-1">
            {funnel.map((step, index) => {
              const previous = index > 0 ? funnel[index - 1] : null;
              const lost =
                previous && previous.sent > 0
                  ? Math.max(0, ((previous.sent - step.sent) / previous.sent) * 100)
                  : undefined;
              const tone = TONE_BY_CHANNEL[step.channel] === "warm" ? "warm" : "accent";
              return (
                <li key={step.step_key}>
                  {index > 0 && <FlowArrow loss={lost} />}
                  <a
                    href={drillHrefFor(step)}
                    onClick={(event) => {
                      event.preventDefault();
                      onStepClick(step);
                    }}
                    title={`Drill into contacts at or past ${step.name || step.step_key}`}
                    className={clsx(
                      "grid grid-cols-[1fr_auto] items-center gap-4 rounded-input border px-3 py-2 transition-colors",
                      selectedStepKey === step.step_key
                        ? "border-accent bg-[var(--ghost-fill)]"
                        : "border-transparent hover:border-line hover:bg-[rgba(255,255,255,.02)]",
                    )}
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="tabular font-mono text-[11px] text-meta">
                          {String(step.index + 1).padStart(2, "0")}
                        </span>
                        <ChannelTag channel={step.channel} />
                        <span className="truncate font-mono text-[12.5px] text-ink">
                          {step.name || step.step_key}
                        </span>
                      </div>
                      <MeasureBar
                        value={step.sent}
                        max={maxSent}
                        tone={tone}
                        label={String(step.sent)}
                      />
                      <p className="tabular mt-1 font-mono text-[11px] text-meta">
                        sent {step.sent} · delivered {step.delivered} · replied {step.replied} ·
                        positive {step.positive_replies}
                      </p>
                    </div>
                    <span className="tabular text-right font-mono text-[12px] text-ink">
                      {step.sent ? pct((step.replied / step.sent) * 100) : "—"}
                    </span>
                  </a>
                </li>
              );
            })}
          </ol>

          <DataTable columns={columns} rows={funnel} rowKey={(row) => row.step_key} dense />
        </div>
      )}
    </Panel>
  );
}
