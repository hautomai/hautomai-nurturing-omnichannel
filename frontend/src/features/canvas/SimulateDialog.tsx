import { useState } from "react";

import { api } from "../../lib/api";
import { useContacts } from "../../lib/hooks";
import { dateTime } from "../../lib/format";
import { Button } from "../../components/ui/Button";
import { Field, Select } from "../../components/ui/Fields";
import { Modal } from "../../components/ui/Feedback";
import { ChannelTag, BulletList } from "../../components/ui/Data";
import { StatusPill } from "../../components/ui/Indicators";
import type { SimulatedStep } from "../../lib/types";

const HYPOTHETICALS = [
  { key: "replied", label: "Prospect replies" },
  { key: "replied_positive", label: "Positive reply" },
  { key: "replied_negative", label: "Negative reply" },
  { key: "meeting_booked", label: "Meeting booked" },
  { key: "unsubscribed", label: "Unsubscribed" },
  { key: "bounced", label: "Hard bounce" },
  { key: "no_phone", label: "No phone number" },
];

/**
 * Simulation (§8.10). Runs the engine's decision path against a real contact
 * without sending or writing events, so the operator can see the cadence and the
 * rendered copy before publishing.
 */
export function SimulateDialog({
  open,
  onClose,
  sequenceId,
  clientId,
}: {
  open: boolean;
  onClose: () => void;
  sequenceId: string;
  clientId: string | null;
}) {
  const contacts = useContacts(clientId, { limit: 200 });
  const [contactId, setContactId] = useState("");
  const [hypotheticals, setHypotheticals] = useState<Record<string, boolean>>({});
  const [steps, setSteps] = useState<SimulatedStep[] | null>(null);
  const [exited, setExited] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function run() {
    if (!contactId) {
      setError("Pick a contact to simulate against.");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const result = await api.simulate(sequenceId, contactId, hypotheticals);
      setSteps(result.timeline);
      setExited(result.exited);
      setWarnings(result.warnings);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Simulation failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Dry run"
      width="max-w-3xl"
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <Button variant="primary" onClick={run} loading={running}>
            Run simulation
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="font-mono text-[12.5px] leading-relaxed text-body-2">
          Nothing is sent and no events are written. Toggle the hypotheticals to see how the next
          touchpoint would change.
        </p>

        <Field label="Contact" htmlFor="simulate-contact" error={error}>
          <Select
            id="simulate-contact"
            value={contactId}
            onChange={(event) => setContactId(event.target.value)}
          >
            <option value="">— select a contact —</option>
            {(contacts.data ?? []).map((contact) => (
              <option key={contact.id} value={contact.id}>
                {contact.first_name} {contact.last_name} · {contact.email ?? contact.phone_e164}
              </option>
            ))}
          </Select>
        </Field>

        <div>
          <p className="mb-2 font-mono text-[11px] uppercase tracking-kicker text-meta">
            Hypothetical reactions
          </p>
          <div className="flex flex-wrap gap-2">
            {HYPOTHETICALS.map((item) => {
              const active = Boolean(hypotheticals[item.key]);
              return (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={active}
                  onClick={() =>
                    setHypotheticals((current) => ({ ...current, [item.key]: !current[item.key] }))
                  }
                  className="focus-visible:outline-none"
                >
                  <StatusPill tone={active ? "accent" : "muted"} label={item.label} showsCheck={active} />
                </button>
              );
            })}
          </div>
        </div>

        {warnings.length > 0 && (
          <BulletList items={warnings.map((text) => ({ text, tone: "warm" as const }))} />
        )}

        {steps && (
          <div className="flex flex-col gap-2">
            <p className="font-mono text-[11px] uppercase tracking-kicker text-meta">
              Projected timeline · {steps.length} step(s)
            </p>
            <ol className="flex flex-col gap-2">
              {steps.map((step, index) => (
                <li
                  key={`${step.step_key}-${index}`}
                  className="rounded-card border border-line bg-[var(--panel-fill)] px-3 py-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <span className="tabular font-mono text-[11px] text-meta">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <ChannelTag channel={step.channel} />
                      <span className="font-mono text-[12.5px] text-ink">{step.step_key}</span>
                    </span>
                    <span className="font-mono text-[11px] text-meta">
                      {step.run_at ? dateTime(step.run_at) : "now"} · {step.outcome || step.step_type}
                    </span>
                  </div>
                  {step.rendered_subject && (
                    <p className="mt-1 font-mono text-[12px] text-ink">{step.rendered_subject}</p>
                  )}
                  {step.rendered_body && (
                    <p className="mt-1 line-clamp-3 font-mono text-[12px] leading-relaxed text-body-2">
                      {step.rendered_body}
                    </p>
                  )}
                  {step.detail && (
                    <p className="mt-1 font-mono text-[11px] text-meta-2">{step.detail}</p>
                  )}
                </li>
              ))}
            </ol>
            <div className="flex items-center gap-2">
              <StatusPill tone={exited ? "muted" : "warm"} label={exited ? "exits" : "still running"} />
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
