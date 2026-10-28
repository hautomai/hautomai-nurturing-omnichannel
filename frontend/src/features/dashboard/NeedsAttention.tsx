import { Link } from "react-router-dom";

import { Panel, PanelSkeleton } from "../../components/ui/Panel";
import { EmptyState } from "../../components/ui/Feedback";
import { StatusPill, type Tone } from "../../components/ui/Indicators";
import { relative } from "../../lib/format";
import type { NeedsAttentionRow } from "../../lib/types";

const SENTIMENT_TONE: Record<string, Tone> = {
  positive: "accent",
  question: "warm",
  negative: "danger",
  neutral: "muted",
};

const SEVERITY_TONE: Record<NeedsAttentionRow["severity"], Tone> = {
  info: "muted",
  warn: "warm",
  danger: "danger",
};

/**
 * Row 4, full width (§22.2). The single ops element, kept as a feed rather than
 * the headline, and the only panel that reads raw events — an indexed query with
 * `LIMIT 50`.
 */
export function NeedsAttention({
  rows,
  loading,
}: {
  rows: NeedsAttentionRow[];
  loading: boolean;
}) {
  return (
    <Panel
      kicker="Needs attention"
      title="Replies, tasks and stalls"
      footer={["Oldest first", `${rows.length} item(s)`]}
    >
      {loading ? (
        <PanelSkeleton rows={4} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="Nothing needs a human"
          description="No unanswered replies, no overdue tasks, and no stalled enrollments."
        />
      ) : (
        <ul className="flex flex-col">
          {rows.map((row, index) => (
            <li key={`${row.kind}-${row.contact_id}-${row.at}-${index}`} className="border-b border-line last:border-b-0">
              <Link
                to={`/contacts/${row.contact_id}`}
                className="grid grid-cols-[auto_1fr_auto] items-center gap-3 py-2.5 transition-colors hover:bg-[rgba(255,255,255,.03)]"
              >
                <StatusPill
                  tone={
                    row.kind === "reply"
                      ? (SENTIMENT_TONE[row.sentiment] ?? "muted")
                      : SEVERITY_TONE[row.severity]
                  }
                  label={row.kind === "reply" ? row.sentiment || "reply" : row.kind}
                />
                <span className="min-w-0">
                  <span className="block truncate font-mono text-[13px] text-ink">
                    {row.contact_label}
                  </span>
                  <span className="block truncate font-mono text-[12px] text-body-2">
                    {row.summary}
                  </span>
                </span>
                <span className="whitespace-nowrap font-mono text-[11px] text-meta">
                  {relative(row.at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
