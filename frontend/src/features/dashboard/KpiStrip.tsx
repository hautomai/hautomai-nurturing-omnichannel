import { MetricChip } from "../../components/ui/Data";
import { num, pct } from "../../lib/format";
import type { OverviewMetrics } from "../../lib/types";

/**
 * Row 1 (§22.2): six chips from `sequence_stats`. Every figure is a rollup, so
 * the dashboard never touches raw events.
 */
export function KpiStrip({ data, loading }: { data?: OverviewMetrics; loading: boolean }) {
  const deltas = data?.deltas ?? {};
  const capacityUsed = data && data.daily_capacity > 0
    ? Math.round((data.sent_today / data.daily_capacity) * 100)
    : 0;

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <MetricChip
        label="Active enrollments"
        value={num(data?.active_enrollments)}
        delta={deltas.active_enrollments}
        loading={loading}
      />
      <MetricChip
        label="Sent today"
        value={num(data?.sent_today)}
        hint={`${capacityUsed}% of ${num(data?.daily_capacity ?? 0)} daily cap`}
        loading={loading}
      />
      <MetricChip
        label="Reply rate"
        value={pct(data?.reply_rate)}
        delta={deltas.reply_rate}
        loading={loading}
      />
      <MetricChip
        label="Positive replies"
        value={num(data?.positive_replies)}
        delta={deltas.positive_replies}
        loading={loading}
      />
      <MetricChip
        label="Meetings booked"
        value={num(data?.meetings)}
        delta={deltas.meetings}
        loading={loading}
      />
      <MetricChip
        label="Live versions"
        value={num(data?.live_versions)}
        delta={deltas.live_versions}
        loading={loading}
      />
    </div>
  );
}
