import clsx from "clsx";
import type { ReactNode } from "react";

import { titleCase } from "../../lib/format";
import { TONE_BY_CHANNEL } from "./Indicators";

/**
 * The KPI strip's unit (§21.4 `.metric`): kicker label, ink figure, and a delta
 * versus the previous equal period in accent (up) or warm (down).
 */
export function MetricChip({
  label,
  value,
  delta,
  hint,
  loading = false,
  className,
}: {
  label: string;
  value: ReactNode;
  delta?: number | null;
  hint?: string;
  loading?: boolean;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "flex min-w-0 flex-col gap-1 rounded-input border border-line bg-[var(--panel-fill)] px-3 py-2.5",
        className,
      )}
    >
      <span className="font-mono text-[10px] uppercase tracking-kicker text-meta">{label}</span>
      {loading ? (
        <span className="h-6 w-16 animate-pulse rounded bg-[rgba(245,243,238,.08)]" />
      ) : (
        <div className="flex items-baseline gap-2">
          <span className="tabular font-display text-[22px] font-bold leading-none tracking-card text-ink">
            {value}
          </span>
          {delta !== undefined && delta !== null && <Delta value={delta} />}
        </div>
      )}
      {hint && <span className="font-mono text-[10px] text-meta-2">{hint}</span>}
    </div>
  );
}

function Delta({ value }: { value: number }) {
  const up = value >= 0;
  return (
    <span
      className={clsx(
        "tabular font-mono text-[11px]",
        up ? "text-accent" : "text-warm",
      )}
      title={`${up ? "+" : ""}${value.toFixed(1)} vs previous period`}
    >
      {up ? "▲" : "▼"} {Math.abs(value).toFixed(1)}
    </span>
  );
}

/** Onboarding checklist / empty-state instructions (§21.4 `.close-step`). */
export function NumberedStep({
  index,
  title,
  children,
  href,
  onClick,
}: {
  index: number;
  title: string;
  children?: ReactNode;
  href?: string;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span className="tabular font-mono text-[13px] font-semibold text-accent">
        {String(index).padStart(2, "0")}
      </span>
      <span className="min-w-0">
        <span className="block font-display text-[15px] font-bold tracking-card text-ink">{title}</span>
        {children && <span className="mt-0.5 block font-mono text-[12px] text-body-2">{children}</span>}
      </span>
    </>
  );

  const className =
    "grid grid-cols-[36px_1fr] items-start gap-3 border-b border-line px-1 py-3 text-left transition-colors last:border-b-0 hover:bg-[rgba(255,255,255,.02)]";

  if (href) {
    return (
      <a href={href} className={className}>
        {content}
      </a>
    );
  }
  return (
    <button type="button" onClick={onClick} className={clsx(className, "w-full")}>
      {content}
    </button>
  );
}

/** `.price-line` + lime bullet: template merge fields, validation errors, notes. */
export function BulletList({
  items,
  tone = "accent",
  className,
}: {
  items: (ReactNode | { tone?: "accent" | "warm" | "danger" | "muted"; text: ReactNode })[];
  tone?: "accent" | "warm" | "danger" | "muted";
  className?: string;
}) {
  const dotTone: Record<string, string> = {
    accent: "bg-accent",
    warm: "bg-warm",
    danger: "bg-danger",
    muted: "bg-[var(--body-2)]",
  };
  return (
    <ul className={clsx("flex flex-col gap-1.5", className)}>
      {items.map((item, index) => {
        const entry = typeof item === "object" && item !== null && "text" in item ? item : null;
        const itemTone = entry?.tone ?? tone;
        const text = entry ? entry.text : (item as ReactNode);
        return (
          <li key={index} className="flex items-start gap-2 font-mono text-[12.5px] leading-relaxed text-body-2">
            <span aria-hidden className={clsx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", dotTone[itemTone])} />
            <span className="min-w-0">{text}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** `.flow-arrow`: the grey connector between funnel steps, with a loss figure. */
export function FlowArrow({ loss }: { loss?: number }) {
  return (
    <div className="flex items-center gap-2 font-mono text-[11px] text-meta">
      <span aria-hidden className="text-[14px] leading-none text-[#696962]">
        ↓
      </span>
      {loss !== undefined && <span className="text-warm">−{loss.toFixed(0)}%</span>}
    </div>
  );
}

export function ChannelTag({ channel }: { channel: string }) {
  const tone = TONE_BY_CHANNEL[channel] ?? "muted";
  return (
    <span
      className={clsx(
        "rounded-pill border border-line px-2 py-0.5 font-mono text-[10px] uppercase tracking-kicker",
        tone === "accent" && "text-accent",
        tone === "warm" && "text-warm",
        tone === "muted" && "text-body-2",
      )}
      title={`#696962 tag in the deck; ${channel}`}
    >
      {channel || "—"}
    </span>
  );
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line py-2 last:border-b-0">
      <span className="font-mono text-[11px] uppercase tracking-kicker text-meta">{label}</span>
      <span className="tabular min-w-0 truncate text-right font-mono text-[13px] text-ink">{value}</span>
    </div>
  );
}

export interface Column<T> {
  key: string;
  header: string;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
  className?: string;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  empty,
  dense = false,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  dense?: boolean;
}) {
  if (rows.length === 0 && empty) return <>{empty}</>;
  return (
    <div className="scrollbar-thin -mx-1 overflow-x-auto px-1">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={clsx(
                  "border-b border-line pb-2 font-mono text-[11px] font-medium uppercase tracking-kicker text-meta",
                  column.align === "right" ? "text-right" : "text-left",
                  column.className,
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={clsx(
                "border-b border-line last:border-b-0",
                onRowClick && "cursor-pointer transition-colors hover:bg-[rgba(255,255,255,.03)]",
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={clsx(
                    "font-mono text-[13px] text-body-2",
                    dense ? "py-2" : "py-2.5",
                    column.align === "right" && "tabular text-right",
                    column.className,
                  )}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Consistent KPI figure rendering for tables. */
export function Figure({ value, muted = false }: { value: number; muted?: boolean }) {
  return (
    <span className={clsx("tabular font-mono", muted ? "text-meta" : "text-ink")}>{value}</span>
  );
}

export function ReasonLabel({ reason }: { reason: string }) {
  return <span className="text-body-2">{titleCase(reason)}</span>;
}
