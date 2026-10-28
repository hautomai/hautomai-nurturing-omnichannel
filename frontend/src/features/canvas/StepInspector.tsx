import { Button } from "../../components/ui/Button";
import { Field, Input, Select, Textarea } from "../../components/ui/Fields";
import { BulletList, ChannelTag, KeyValue } from "../../components/ui/Data";
import { Panel } from "../../components/ui/Panel";
import { StatusPill } from "../../components/ui/Indicators";
import type { FallbackMode, SequenceGraph, StepNode, Template } from "../../lib/types";
import { successorsOf } from "./layout";

const NODE_TYPE_LABELS: Record<StepNode["type"], string> = {
  send_email: "Send email",
  send_sms: "Send SMS",
  delay: "Wait",
  condition: "Condition",
  notify_slack: "Slack alert",
  human_task: "Human task",
  exit: "Exit",
};

export function StepInspector({
  step,
  graph,
  templates,
  issues,
  onChange,
  onDelete,
  onDuplicate,
  readOnly,
}: {
  step: StepNode;
  graph: SequenceGraph;
  templates: Template[];
  issues: { level: "error" | "warning"; message: string }[];
  onChange: (patch: Partial<StepNode>) => void;
  onDelete: () => void;
  onDuplicate: () => void;
  readOnly: boolean;
}) {
  const stepKeys = (graph.steps ?? []).map((row) => row.key).filter((key) => key !== step.key);
  const emailTemplates = templates.filter((template) => template.channel === "email");
  const smsTemplates = templates.filter((template) => template.channel === "sms");
  const successorOptions = (includeNone: boolean) => (
    <>
      {includeNone && <option value="">— none —</option>}
      {stepKeys.map((key) => (
        <option key={key} value={key}>
          {key}
        </option>
      ))}
    </>
  );

  return (
    <Panel
      kicker="Step inspector"
      title={step.name || step.key}
      actions={
        <>
          <Button size="sm" onClick={onDuplicate} disabled={readOnly}>
            Duplicate
          </Button>
          <Button size="sm" variant="danger" onClick={onDelete} disabled={readOnly}>
            Delete
          </Button>
        </>
      }
      footer={[step.key, NODE_TYPE_LABELS[step.type]]}
      className="min-w-0"
    >
      <div className="flex flex-col gap-4">
        {issues.length > 0 && (
          <BulletList
            items={issues.map((issue) => ({
              text: issue.message,
              tone: issue.level === "error" ? "danger" : "warm",
            }))}
          />
        )}

        <div className="flex flex-wrap items-center gap-2">
          <ChannelTag channel={step.type === "send_email" ? "email" : step.type === "send_sms" ? "sms" : ""} />
          <StatusPill
            tone={step.type === "exit" ? "muted" : "accent"}
            label={NODE_TYPE_LABELS[step.type]}
          />
        </div>

        <Field label="Step key" htmlFor="step-key" hint="Referenced by the funnel and the timeline.">
          <Input
            id="step-key"
            value={step.key}
            disabled={readOnly}
            onChange={(event) => onChange({ key: event.target.value })}
          />
        </Field>

        <Field label="Name" htmlFor="step-name">
          <Input
            id="step-name"
            value={step.name}
            disabled={readOnly}
            onChange={(event) => onChange({ name: event.target.value })}
          />
        </Field>

        <Field label="Type" htmlFor="step-type">
          <Select
            id="step-type"
            value={step.type}
            disabled={readOnly}
            onChange={(event) => onChange({ type: event.target.value as StepNode["type"] })}
          >
            {Object.entries(NODE_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>

        {(step.type === "send_email" || step.type === "send_sms") && (
          <>
            <Field label="Template" htmlFor="step-template">
              <Select
                id="step-template"
                value={step.template_key ?? ""}
                disabled={readOnly}
                onChange={(event) => onChange({ template_key: event.target.value })}
              >
                <option value="">— select a template —</option>
                {(step.type === "send_email" ? emailTemplates : smsTemplates).map((template) => (
                  <option key={template.id} value={template.key}>
                    {template.key} · {template.name || "untitled"}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Channel fallback"
              htmlFor="step-fallback"
              hint="Used when the contact has no address on this channel."
            >
              <Select
                id="step-fallback"
                value={step.fallback ?? "skip"}
                disabled={readOnly}
                onChange={(event) => onChange({ fallback: event.target.value as FallbackMode })}
              >
                <option value="skip">Skip the step</option>
                <option value="send_email">Send the email instead</option>
                <option value="send_sms">Send the SMS instead</option>
              </Select>
            </Field>

            {step.fallback && step.fallback !== "skip" && (
              <Field label="Fallback template" htmlFor="step-fallback-template">
                <Select
                  id="step-fallback-template"
                  value={step.fallback_template_key ?? ""}
                  disabled={readOnly}
                  onChange={(event) => onChange({ fallback_template_key: event.target.value })}
                >
                  <option value="">— select a template —</option>
                  {(step.fallback === "send_email" ? emailTemplates : smsTemplates).map((template) => (
                    <option key={template.id} value={template.key}>
                      {template.key}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
          </>
        )}

        {step.type === "delay" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Wait for" htmlFor="step-amount">
              <Input
                id="step-amount"
                type="number"
                min={0}
                step="0.5"
                value={step.amount ?? 1}
                disabled={readOnly}
                onChange={(event) => onChange({ amount: Number(event.target.value) })}
              />
            </Field>
            <Field label="Unit" htmlFor="step-unit">
              <Select
                id="step-unit"
                value={step.unit ?? "days"}
                disabled={readOnly}
                onChange={(event) => onChange({ unit: event.target.value as StepNode["unit"] })}
              >
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </Select>
            </Field>
          </div>
        )}

        {step.type === "condition" && (
          <>
            <Field
              label="Predicate type"
              htmlFor="step-predicate"
              hint="Evaluated against the enrollment's recorded signals, never against an open."
            >
              <Select
                id="step-predicate"
                value={String(step.predicate?.type ?? "replied")}
                disabled={readOnly}
                onChange={(event) =>
                  onChange({ predicate: { ...(step.predicate ?? {}), type: event.target.value } })
                }
              >
                <option value="replied">Replied</option>
                <option value="channel_available">Channel available</option>
                <option value="has_phone">Has phone number</option>
                <option value="tagged">Tagged</option>
              </Select>
            </Field>
            <Field label="If true →" htmlFor="step-on-true">
              <Select
                id="step-on-true"
                value={step.on_true ?? ""}
                disabled={readOnly}
                onChange={(event) => onChange({ on_true: event.target.value })}
              >
                {successorOptions(true)}
              </Select>
            </Field>
            <Field label="If false →" htmlFor="step-on-false">
              <Select
                id="step-on-false"
                value={step.on_false ?? ""}
                disabled={readOnly}
                onChange={(event) => onChange({ on_false: event.target.value })}
              >
                {successorOptions(true)}
              </Select>
            </Field>
          </>
        )}

        {step.type === "notify_slack" && (
          <Field label="Message" htmlFor="step-text">
            <Textarea
              id="step-text"
              value={step.text ?? ""}
              disabled={readOnly}
              onChange={(event) => onChange({ text: event.target.value })}
            />
          </Field>
        )}

        {step.type === "human_task" && (
          <>
            <Field label="Task title" htmlFor="step-title">
              <Input
                id="step-title"
                value={step.title ?? ""}
                disabled={readOnly}
                onChange={(event) => onChange({ title: event.target.value })}
              />
            </Field>
            <Field label="Instructions" htmlFor="step-instructions">
              <Textarea
                id="step-instructions"
                value={step.instructions ?? ""}
                disabled={readOnly}
                onChange={(event) => onChange({ instructions: event.target.value })}
              />
            </Field>
            <Field label="Timeout (hours)" htmlFor="step-timeout">
              <Input
                id="step-timeout"
                type="number"
                min={1}
                value={step.timeout_hours ?? 24}
                disabled={readOnly}
                onChange={(event) => onChange({ timeout_hours: Number(event.target.value) })}
              />
            </Field>
          </>
        )}

        {step.type === "exit" ? (
          <Field label="Exit reason" htmlFor="step-reason">
            <Select
              id="step-reason"
              value={step.reason ?? "completed"}
              disabled={readOnly}
              onChange={(event) => onChange({ reason: event.target.value })}
            >
              {["completed", "replied", "meeting_booked", "unsubscribed", "bounced_hard"].map((reason) => (
                <option key={reason} value={reason}>
                  {reason}
                </option>
              ))}
            </Select>
          </Field>
        ) : step.type !== "condition" ? (
          <Field label="Next step" htmlFor="step-next">
            <Select
              id="step-next"
              value={step.next ?? ""}
              disabled={readOnly}
              onChange={(event) => onChange({ next: event.target.value })}
            >
              {successorOptions(true)}
            </Select>
          </Field>
        ) : null}

        <div className="rounded-card border border-line px-3">
          <KeyValue label="Draws below" value={successorsOf(step).map((row) => row.key).join(", ") || "—"} />
        </div>
      </div>
    </Panel>
  );
}
