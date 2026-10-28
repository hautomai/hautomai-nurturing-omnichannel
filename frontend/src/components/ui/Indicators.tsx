import clsx from "clsx";
import type { ReactNode } from "react";

type Tone = "accent" | "warm" | "danger" | "muted";

const TONES: Record<Tone, { text: string; border: string; fill: string; dot: string }> = {
  accent: {
    text: "text-accent",
    border: "border-[var(--ghost-border)]",
    fill: "bg-[var(--ghost-fill)]",
    dot: "bg-accent",
  },
  warm: {
    text: "text-warm",
    border: "border-[rgba(243,201,105,.3)]",
    fill: "bg-[rgba(243,201,105,.07)]",
    dot: "bg-warm",
  },
  danger: {
    text: "text-danger",
    border: "border-[rgba(255,125,110,.35)]",
    fill: "bg-[rgba(255,125,110,.08)]",
    dot: "bg-danger",
  },
  muted: {
    text: "text-body-2",
    border: "border-line",
    fill: "bg-transparent",
    dot: "bg-[var(--body-2)]",
  },
};

/**
 * Status is never encoded by colour alone (§21.8): every pill carries its label,
 * and `showsCheck` renders the deck's signed-state tick for success.
 */
export function StatusPill({
  tone = "muted",
  label,
  showsCheck = false,
  glow = false,
  className,
}: {
  tone?: Tone;
  label: string;
  showsCheck?: boolean;
  glow?: boolean;
  className?: string;
}) {
  const style = TONES[tone];
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-2 rounded-pill border px-2.5 py-1",
        "font-mono text-[11px] uppercase tracking-kicker whitespace-nowrap",
        style.text,
        style.border,
        style.fill,
        className,
      )}
    >
      <span
        aria-hidden
        className={clsx("h-2 w-2 shrink-0 rounded-full", style.dot, glow && "shadow-glow")}
      />
      {showsCheck && <span aria-hidden>✓</span>}
      {label}
    </span>
  );
}

/** The deck's glowing pill dot, reused as a data-freshness signal (§22.3). */
export function LiveDot({
  tone = "accent",
  label,
  pulsing = true,
}: {
  tone?: Tone;
  label?: string;
  pulsing?: boolean;
}) {
  const style = TONES[tone];
  return (
    <span className="inline-flex items-center gap-2 font-mono text-[11px] uppercase tracking-kicker text-meta">
      <span
        aria-hidden
        className={clsx(
          "h-2 w-2 rounded-full shadow-glow",
          style.dot,
          pulsing && "animate-pulseglow",
        )}
      />
      {label}
    </span>
  );
}

/** Thin lime bar on a `--line` track, used for inbox caps (§22.2 row 3). */
export function ProgressBar({
  value,
  max,
  tone = "accent",
  className,
}: {
  value: number;
  max: number;
  tone?: Extract<Tone, "accent" | "warm" | "danger">;
  className?: string;
}) {
  const percent = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
      className={clsx("h-1.5 w-full overflow-hidden rounded-pill bg-[rgba(245,243,238,.12)]", className)}
    >
      <div
        className={clsx(
          "h-full rounded-pill transition-[width] duration-500",
          tone === "accent" && "bg-accent",
          tone === "warm" && "bg-warm",
          tone === "danger" && "bg-danger",
        )}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

export function Banner({
  tone = "warm",
  title,
  children,
  action,
}: {
  tone?: Tone;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  const style = TONES[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={clsx(
        "flex flex-wrap items-center justify-between gap-3 rounded-card border px-4 py-3",
        style.border,
        style.fill,
      )}
    >
      <div className="min-w-0">
        <p className={clsx("font-mono text-[12px] font-semibold uppercase tracking-kicker", style.text)}>
          {title}
        </p>
        {children && <p className="mt-1 font-mono text-[12px] text-body-2">{children}</p>}
      </div>
      {action}
    </div>
  );
}

/** Horizontal measure bar used by the failure table and the funnel. */
export function MeasureBar({
  value,
  max,
  tone = "accent",
  label,
}: {
  value: number;
  max: number;
  tone?: Extract<Tone, "accent" | "warm" | "danger">;
  label?: string;
}) {
  const percent = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="h-2 min-w-0 flex-1 rounded-pill bg-[rgba(245,243,238,.08)]">
        <div
          className={clsx(
            "h-full rounded-pill",
            tone === "accent" && "bg-accent",
            tone === "warm" && "bg-warm",
            tone === "danger" && "bg-danger",
          )}
          style={{ width: `${Math.min(100, percent)}%` }}
        />
      </div>
      {label && <span className="tabular w-14 text-right font-mono text-[12px] text-ink">{label}</span>}
    </div>
  );
}

export function Divider({ className }: { className?: string }) {
  return <hr className={clsx("border-0 border-t border-line", className)} />;
}

export const TONE_BY_CHANNEL: Record<string, Tone> = {
  email: "accent",
  sms: "warm",
  slack: "muted",
};

export type { Tone };
