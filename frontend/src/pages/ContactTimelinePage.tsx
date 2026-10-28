import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import {
  useContact,
  useContactEnrollments,
  useEnrollContact,
  useEnrollmentAction,
  useSequences,
  useTimeline,
} from "../lib/hooks";
import { dateTime, relative } from "../lib/format";
import { Button } from "../components/ui/Button";
import { Panel, PanelSkeleton } from "../components/ui/Panel";
import { ChannelTag, DataTable, KeyValue, type Column } from "../components/ui/Data";
import { StatusPill, type Tone } from "../components/ui/Indicators";
import { EmptyState, ErrorState, Modal } from "../components/ui/Feedback";
import { Field, Select } from "../components/ui/Fields";
import type { Enrollment, TimelineEntry } from "../lib/types";

const KIND_TONE: Record<string, Tone> = {
  event: "muted",
  message: "accent",
  inbound: "accent",
  decision: "warm",
  task: "warm",
};

export function ContactTimelinePage() {
  const { contactId = "" } = useParams();
  const { clientId } = useWorkspace();
  const contact = useContact(contactId);
  const timeline = useTimeline(contactId);
  const enrollments = useContactEnrollments(contactId);
  const sequences = useSequences(clientId);
  const enroll = useEnrollContact();
  const action = useEnrollmentAction();

  const [enrolling, setEnrolling] = useState(false);
  const [sequenceId, setSequenceId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const columns: Column<TimelineEntry>[] = [
    {
      key: "at",
      header: "When",
      render: (row) => (
        <span>
          <span className="block whitespace-nowrap text-body-2">{dateTime(row.at)}</span>
          <span className="block font-mono text-[10px] text-meta-2">{relative(row.at)}</span>
        </span>
      ),
    },
    {
      key: "kind",
      header: "Kind",
      render: (row) => <StatusPill tone={KIND_TONE[row.kind] ?? "muted"} label={row.kind} />,
    },
    { key: "channel", header: "Channel", render: (row) => <ChannelTag channel={row.channel} /> },
    {
      key: "title",
      header: "What happened",
      render: (row) => (
        <span>
          <span className="block text-ink">{row.title}</span>
          {row.status && <span className="block font-mono text-[11px] text-meta">{row.status}</span>}
        </span>
      ),
    },
    {
      key: "detail",
      header: "Detail",
      render: (row) => (
        <span className="block max-w-[420px] truncate font-mono text-[11px] text-meta-2">
          {Object.entries(row.detail ?? {})
            .map(([key, value]) => `${key}=${String(value)}`)
            .join(" · ") || "—"}
        </span>
      ),
    },
  ];

  if (contact.error) {
    return (
      <div className="mx-auto max-w-3xl">
        <ErrorState message={(contact.error as Error).message} onRetry={() => contact.refetch()} />
      </div>
    );
  }

  const person = contact.data;

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/contacts" className="font-mono text-[11px] text-meta hover:text-ink">
            ← Contacts
          </Link>
          <h1 className="mt-1 font-display text-[24px] font-bold tracking-title text-ink">
            {person ? [person.first_name, person.last_name].filter(Boolean).join(" ") || person.email : "Contact"}
          </h1>
          <p className="mt-1 font-mono text-[12px] text-body-2">
            {person?.job_title ? `${person.job_title} · ` : ""}
            {person?.company || "—"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {person?.email && <StatusPill tone="accent" label={person.email} />}
          {person?.phone_e164 && <StatusPill tone="warm" label={person.phone_e164} />}
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              setSequenceId(sequences.data?.[0]?.id ?? "");
              setEnrolling(true);
            }}
          >
            Enroll
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_2fr]">
        <div className="flex flex-col gap-5">
          <Panel kicker="Profile" title="Contact">
            {contact.isLoading ? (
              <PanelSkeleton rows={5} />
            ) : (
              <div>
                <KeyValue label="Email" value={person?.email ?? "—"} />
                <KeyValue label="Phone" value={person?.phone_e164 ?? "—"} />
                <KeyValue label="Company" value={person?.company || "—"} />
                <KeyValue label="Title" value={person?.job_title || "—"} />
                <KeyValue label="Timezone" value={person?.timezone || "—"} />
                <KeyValue label="Source" value={person?.source ?? "—"} />
                <KeyValue label="Tags" value={person?.tags?.join(", ") || "—"} />
                <KeyValue label="Added" value={person ? dateTime(person.created_at) : "—"} />
              </div>
            )}
          </Panel>

          <Panel kicker="Enrollments" title="Sequence membership">
            {enrollments.isLoading ? (
              <PanelSkeleton rows={3} />
            ) : (enrollments.data ?? []).length === 0 ? (
              <p className="font-mono text-[12.5px] text-body-2">
                Not in a sequence. Enroll to start the cadence.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {(enrollments.data ?? []).map((enrollment) => (
                  <EnrollmentCard
                    key={enrollment.id}
                    enrollment={enrollment}
                    busy={action.isPending}
                    onAction={(nextAction) =>
                      action.mutate({
                        action: nextAction,
                        enrollmentId: enrollment.id,
                        reason: nextAction === "pause" ? "paused from the timeline" : undefined,
                      })
                    }
                  />
                ))}
              </div>
            )}
          </Panel>
        </div>

        <Panel
          kicker="Timeline"
          title="Every touch and signal"
          bodyClassName="px-0 py-0"
          footer={["Newest first", `${timeline.data?.length ?? 0} entries`]}
        >
          {timeline.isLoading ? (
            <div className="p-5">
              <PanelSkeleton rows={6} />
            </div>
          ) : (timeline.data ?? []).length === 0 ? (
            <div className="p-5">
              <EmptyState
                title="Nothing recorded yet"
                description="Sends, replies, decisions and human tasks all land here in order."
              />
            </div>
          ) : (
            <DataTable
              columns={columns}
              rows={[...(timeline.data ?? [])].reverse()}
              rowKey={(row) => `${row.kind}-${row.at}-${row.title}`}
            />
          )}
        </Panel>
      </div>

      <Modal
        open={enrolling}
        title="Enroll in a sequence"
        onClose={() => setEnrolling(false)}
        footer={
          <>
            <Button onClick={() => setEnrolling(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={enroll.isPending}
              onClick={() => {
                setError(null);
                enroll.mutate(
                  { contactId, sequenceId },
                  {
                    onSuccess: () => setEnrolling(false),
                    onError: (err) =>
                      setError(
                        err instanceof Error
                          ? err.message
                          : "Could not enroll — the contact may already be active.",
                      ),
                  },
                );
              }}
            >
              Enroll
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <Field label="Sequence" htmlFor="enroll-sequence" error={error}>
            <Select
              id="enroll-sequence"
              value={sequenceId}
              onChange={(event) => setSequenceId(event.target.value)}
            >
              <option value="">— select a sequence —</option>
              {(sequences.data ?? [])
                .filter((sequence) => sequence.status === "active")
                .map((sequence) => (
                  <option key={sequence.id} value={sequence.id}>
                    {sequence.name}
                  </option>
                ))}
            </Select>
          </Field>
          <p className="font-mono text-[12px] leading-relaxed text-body-2">
            Only one active sequence per contact per channel. A contact still in another sequence is
            rejected with a 409 rather than silently double-enrolled.
          </p>
        </div>
      </Modal>
    </div>
  );
}

function EnrollmentCard({
  enrollment,
  busy,
  onAction,
}: {
  enrollment: Enrollment;
  busy: boolean;
  onAction: (action: "pause" | "resume" | "exit" | "send-next" | "skip-wait") => void;
}) {
  const tone: Tone =
    enrollment.status === "active"
      ? "accent"
      : enrollment.status === "paused"
        ? "warm"
        : enrollment.status === "failed"
          ? "danger"
          : "muted";

  const open = enrollment.status === "active" || enrollment.status === "pending";

  return (
    <div className="rounded-card border border-line px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusPill tone={tone} label={enrollment.status} glow={enrollment.status === "active"} />
        <span className="font-mono text-[11px] text-meta">
          step {enrollment.current_step_key ?? "—"} · {enrollment.touches} touch(es)
        </span>
      </div>
      <div className="mt-2">
        <KeyValue label="Lane" value={enrollment.channel_lane || "—"} />
        <KeyValue
          label="Next touch"
          value={enrollment.next_run_at ? `${dateTime(enrollment.next_run_at)} (${relative(enrollment.next_run_at)})` : "—"}
        />
        {enrollment.pause_reason && <KeyValue label="Paused because" value={enrollment.pause_reason} />}
        {enrollment.exit_reason && <KeyValue label="Exited because" value={enrollment.exit_reason} />}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {open ? (
          <>
            <Button size="sm" onClick={() => onAction("pause")} disabled={busy}>
              Pause
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onAction("send-next")} disabled={busy}>
              Send next now
            </Button>
            <Button size="sm" onClick={() => onAction("skip-wait")} disabled={busy}>
              Skip the wait
            </Button>
            <Button size="sm" variant="danger" onClick={() => onAction("exit")} disabled={busy}>
              Exit
            </Button>
          </>
        ) : enrollment.status === "paused" ? (
          <Button size="sm" variant="primary" onClick={() => onAction("resume")} disabled={busy}>
            Resume
          </Button>
        ) : (
          <span className="font-mono text-[11px] text-meta-2">Closed — history only.</span>
        )}
      </div>
    </div>
  );
}
