import clsx from "clsx";
import type { ReactNode } from "react";

import { Skeleton } from "./Feedback";

type Variant = "panel" | "raised" | "bare";

interface PanelProps {
  title?: ReactNode;
  /** Small uppercase label above the title, per §21.3. */
  kicker?: string;
  /** Right-aligned controls in the header row (selectors, buttons). */
  actions?: ReactNode;
  /** Left/right caption pair at the bottom, from the deck's `.footer`. */
  footer?: [ReactNode, ReactNode];
  variant?: Variant;
  /** Entrance animation. Off by default so refreshes never re-animate (§21.5). */
  animate?: boolean;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}

/**
 * The base surface for every dashboard and timeline block (§21.4).
 *
 * Dense panels step down to an 18px radius; hero/service panels keep 28px.
 * `loading` renders a skeleton in place of the body so a panel never collapses
 * while data is in flight.
 */
export function Panel({
  title,
  kicker,
  actions,
  footer,
  variant = "panel",
  animate = false,
  className,
  bodyClassName,
  children,
}: PanelProps) {
  return (
    <section
      className={clsx(
        "relative flex min-w-0 flex-col border border-line",
        variant === "panel" && "bg-[var(--panel-fill)]",
        variant === "raised" && "bg-panel-2",
        variant === "bare" && "bg-transparent",
        // A "panel" is the hero treatment at 28px; raised/bare are dense.
        variant === "panel" ? "rounded-panel" : "rounded-card",
        variant !== "bare" && "shadow-panel",
        animate && "rise",
        className,
      )}
    >
      {(title || actions || kicker) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {kicker && (
              <p className="mb-1 font-mono text-[11px] uppercase tracking-kicker text-meta">{kicker}</p>
            )}
            {title && (
              <h2 className="truncate font-display text-[18px] font-bold tracking-card text-ink">
                {title}
              </h2>
            )}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx("min-w-0 flex-1 px-5 py-4", bodyClassName)}>{children}</div>
      {footer && (
        <footer className="flex items-center justify-between gap-3 border-t border-line px-5 py-3 font-mono text-[12px] text-meta-2">
          <span className="min-w-0 truncate">{footer[0]}</span>
          <span className="min-w-0 truncate text-right">{footer[1]}</span>
        </footer>
      )}
    </section>
  );
}

/** Panel-level loading placeholder, shaped like a body of rows. */
export function PanelSkeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={clsx("flex flex-col gap-2", className)}>
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-6 w-full" />
      ))}
    </div>
  );
}
