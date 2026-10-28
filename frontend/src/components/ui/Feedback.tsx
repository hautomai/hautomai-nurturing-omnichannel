import clsx from "clsx";
import { useEffect, type ReactNode } from "react";

import { Button } from "./Button";
import { NumberedStep } from "./Data";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={clsx("animate-pulse rounded bg-[rgba(245,243,238,.08)]", className)}
    />
  );
}

export function EmptyState({
  title,
  description,
  steps,
  action,
  className,
}: {
  title: string;
  description?: string;
  steps?: { title: string; body?: string; onClick?: () => void; href?: string }[];
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("flex flex-col gap-4 py-6", className)}>
      <div>
        <p className="font-display text-[16px] font-bold tracking-card text-ink">{title}</p>
        {description && <p className="mt-1 font-mono text-[12.5px] text-body-2">{description}</p>}
      </div>
      {steps && steps.length > 0 && (
        <div className="rounded-card border border-line px-3">
          {steps.map((step, index) => (
            <NumberedStep
              key={step.title}
              index={index + 1}
              title={step.title}
              href={step.href}
              onClick={step.onClick}
            >
              {step.body}
            </NumberedStep>
          ))}
        </div>
      )}
      {action}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
  className,
}: {
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={clsx(
        "flex flex-wrap items-center justify-between gap-3 rounded-card border border-[rgba(255,125,110,.35)] bg-[rgba(255,125,110,.08)] px-4 py-3",
        className,
      )}
    >
      <div className="min-w-0">
        <p className="font-mono text-[12px] font-semibold uppercase tracking-kicker text-danger">
          Could not load
        </p>
        <p className="mt-1 font-mono text-[12px] text-body-2">{message}</p>
      </div>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  width = "max-w-lg",
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
      <div
        aria-hidden
        className="absolute inset-0 bg-[rgba(0,0,0,.7)] backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={clsx(
          "rise relative z-10 flex w-full flex-col rounded-panel border border-line bg-panel shadow-panel",
          width,
        )}
      >
        <header className="flex items-center justify-between gap-4 border-b border-line px-5 py-4">
          <h2 className="font-display text-[18px] font-bold tracking-card text-ink">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-button border border-line px-2.5 py-1 font-mono text-[12px] text-meta transition-colors hover:text-ink"
          >
            ✕
          </button>
        </header>
        <div className="scrollbar-thin max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 border-t border-line px-5 py-4">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}
