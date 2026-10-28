import clsx from "clsx";

import { Panel, PanelSkeleton } from "../../components/ui/Panel";
import { BulletList, KeyValue } from "../../components/ui/Data";
import { StatusPill } from "../../components/ui/Indicators";
import { num, pct } from "../../lib/format";
import type { ChannelComparisonRow } from "../../lib/types";

/**
 * Row 2, one-third width (§22.2). Side-by-side columns with `.price-line`
 * bullets carrying the qualitative read, so the numbers are never the whole
 * story.
 */
export function ChannelComparison({
  rows,
  loading,
}: {
  rows: ChannelComparisonRow[];
  loading: boolean;
}) {
  const email = rows.find((row) => row.channel === "email");
  const sms = rows.find((row) => row.channel === "sms");

  const notes = buildNotes(email, sms);

  return (
    <Panel
      kicker="Channel comparison"
      title="Email vs SMS"
      footer={["Reply rates are per send", "Twilio + rotated SMTP pool"]}
    >
      {loading ? (
        <PanelSkeleton rows={5} />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            {[
              { label: "Email", row: email, tone: "accent" as const },
              { label: "SMS", row: sms, tone: "warm" as const },
            ].map(({ label, row, tone }) => (
              <div
                key={label}
                className={clsx(
                  "rounded-card border border-line p-3",
                  tone === "warm" && "bg-[rgba(243,201,105,.04)]",
                )}
              >
                <StatusPill tone={tone} label={label} />
                <div className="mt-3">
                  <KeyValue label="Sends" value={num(row?.sent)} />
                  <KeyValue label="Delivered" value={num(row?.delivered)} />
                  <KeyValue label="Replies" value={num(row?.replies)} />
                  <KeyValue label="Reply rate" value={pct(row?.reply_rate)} />
                  <KeyValue label="Positive rate" value={pct(row?.positive_rate)} />
                  <KeyValue label="Meetings" value={num(row?.meetings)} />
                </div>
              </div>
            ))}
          </div>

          <BulletList items={notes} />
        </div>
      )}
    </Panel>
  );
}

function buildNotes(
  email: ChannelComparisonRow | undefined,
  sms: ChannelComparisonRow | undefined,
): { text: string; tone?: "accent" | "warm" | "danger" | "muted" }[] {
  const notes: { text: string; tone?: "accent" | "warm" | "danger" | "muted" }[] = [];

  if (email && sms) {
    if ((sms.reply_rate ?? 0) > (email.reply_rate ?? 0)) {
      notes.push({ text: "SMS drives replies; email drives meetings.", tone: "warm" });
    } else {
      notes.push({ text: "Email drives replies; SMS is the cheap nudge.", tone: "accent" });
    }
    if ((email.meetings ?? 0) > (sms.meetings ?? 0)) {
      notes.push({ text: "Meetings attribute mostly to email touches.", tone: "accent" });
    }
  } else if (email) {
    notes.push({ text: "Email only — no SMS sends in this period.", tone: "muted" });
  } else if (sms) {
    notes.push({ text: "SMS only — no email sends in this period.", tone: "muted" });
  } else {
    notes.push({ text: "No sends recorded in this period.", tone: "muted" });
  }

  return notes;
}
