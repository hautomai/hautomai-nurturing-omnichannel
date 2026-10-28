import clsx from "clsx";
import { Handle, Position, type NodeProps } from "@xyflow/react";

import { StatusPill, type Tone } from "../../components/ui/Indicators";
import type { CanvasNode } from "./layout";

const NODE_TONE: Record<string, Tone> = {
  send_email: "accent",
  send_sms: "warm",
  delay: "muted",
  condition: "muted",
  notify_slack: "muted",
  human_task: "muted",
  exit: "muted",
};

const NODE_GLYPH: Record<string, string> = {
  send_email: "✉",
  send_sms: "▤",
  delay: "◷",
  condition: "⋔",
  notify_slack: "◆",
  human_task: "☺",
  exit: "⏹",
};

/**
 * A single step on the canvas (§21.4 `.node`). Every node has one target handle
 * at the top and, unless it is terminal, a source handle at the bottom; the
 * horizontal offset of the source handles on a condition node is what makes the
 * true/false split legible.
 */
export function StepNodeCard({ data, selected }: NodeProps<CanvasNode>) {
  const { step, channel, validation } = data;
  const tone = NODE_TONE[step.type] ?? "muted";
  const terminal = step.type === "exit";
  const errors = validation?.filter((issue) => issue.level === "error") ?? [];
  const warnings = validation?.filter((issue) => issue.level === "warning") ?? [];

  const subtitle = describe(step);

  return (
    <div
      className={clsx(
        "group w-[248px] rounded-card border bg-[var(--panel-fill)] px-3 py-2.5 backdrop-blur transition-colors",
        selected ? "border-accent" : errors.length > 0 ? "border-[rgba(255,125,110,.55)]" : "border-line",
        selected && "shadow-[0_0_0_1px_var(--accent)]",
      )}
    >
      {!terminal && <Handle type="target" position={Position.Top} />}

      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2">
          <span aria-hidden className={clsx("text-[13px]", tone === "accent" && "text-accent", tone === "warm" && "text-warm", tone === "muted" && "text-body-2")}>
            {NODE_GLYPH[step.type] ?? "•"}
          </span>
          <span className="truncate font-mono text-[12.5px] text-ink">{step.name || step.key}</span>
        </span>
        {channel && (
          <span
            className={clsx(
              "font-mono text-[10px] uppercase tracking-kicker",
              channel === "email" ? "text-accent" : channel === "sms" ? "text-warm" : "text-meta",
            )}
          >
            {channel}
          </span>
        )}
      </div>

      {subtitle && <p className="mt-1 truncate font-mono text-[11px] text-meta">{subtitle}</p>}

      {(errors.length > 0 || warnings.length > 0) && (
        <div className="mt-2">
          {errors.length > 0 ? (
            <StatusPill tone="danger" label={`${errors.length} error(s)`} />
          ) : (
            <StatusPill tone="warm" label={`${warnings.length} warning(s)`} />
          )}
        </div>
      )}

      {!terminal && step.type !== "condition" && <Handle type="source" position={Position.Bottom} />}
      {step.type === "condition" && (
        <>
          <Handle
            id="on_true"
            type="source"
            position={Position.Bottom}
            style={{ left: "32%" }}
          />
          <Handle
            id="on_false"
            type="source"
            position={Position.Bottom}
            style={{ left: "68%" }}
          />
        </>
      )}
    </div>
  );
}

function describe(step: CanvasNode["data"]["step"]): string {
  switch (step.type) {
    case "send_email":
    case "send_sms":
      return step.template_key ? `template: ${step.template_key}` : "no template selected";
    case "delay":
      return `wait ${step.amount ?? 1} ${step.unit ?? "days"}`;
    case "condition":
      return `if ${String(step.predicate?.type ?? "condition")}`;
    case "notify_slack":
      return "alert the team";
    case "human_task":
      return step.title || "manual step";
    case "exit":
      return `exit · ${step.reason ?? "completed"}`;
    default:
      return "";
  }
}
