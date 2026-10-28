import { Panel, PanelSkeleton } from "../../components/ui/Panel";
import { EmptyState } from "../../components/ui/Feedback";
import { MeasureBar } from "../../components/ui/Indicators";
import { ReasonLabel } from "../../components/ui/Data";
import { num } from "../../lib/format";
import type { FailureRow } from "../../lib/types";

/**
 * Row 3, half width (§22.2). Danger for hard failures, warm for skips and
 * substitutions, accent for substitutions that still landed. The split lives in
 * `toneFor` so the colour rule is stated once.
 */
export function FailureReport({ rows, loading }: { rows: FailureRow[]; loading: boolean }) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  const sorted = [...rows].sort((a, b) => b.count - a.count);

  return (
    <Panel
      kicker="Failure & skip reporting"
      title="Where touches go sideways"
      footer={["Counts are per attempt", `${sorted.length} distinct reasons`]}
    >
      {loading ? (
        <PanelSkeleton rows={5} />
      ) : sorted.length === 0 ? (
        <EmptyState
          title="No failures or skips"
          description="Every scheduled touch either sent or is still pending."
        />
      ) : (
        <div className="flex flex-col gap-3">
          {sorted.map((row) => (
            <div key={`${row.reason}-${row.channel}`} className="grid grid-cols-[minmax(140px,1fr)_2fr] items-center gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <ReasonLabel reason={row.reason} />
                {row.channel && (
                  <span className="font-mono text-[10px] uppercase tracking-kicker text-meta">
                    {row.channel}
                  </span>
                )}
              </div>
              <MeasureBar value={row.count} max={max} tone={toneFor(row.reason)} label={num(row.count)} />
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function toneFor(reason: string): "accent" | "warm" | "danger" {
  if (["bounce", "inbox_paused", "orphaned_by_version", "suppressed"].includes(reason)) {
    return "danger";
  }
  if (["substituted", "cap_reached", "collision"].includes(reason)) return "warm";
  return "accent";
}
