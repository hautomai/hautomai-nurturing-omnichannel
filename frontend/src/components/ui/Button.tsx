import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "ghost" | "neutral" | "danger";
type Size = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  /** Leading icon glyph. Kept as a node so callers pass text/SVG, not classes. */
  icon?: ReactNode;
  loading?: boolean;
}

/**
 * Exactly four buttons exist (§21.4):
 *   primary  — lime fill, dark text (`.cta`)
 *   ghost    — lime-tinted ghost that inverts to lime on hover (`.sign-btn`)
 *   neutral  — plain bordered ghost (`.sign-btn.alt`)
 *   danger   — `--danger` on a transparent fill
 */
const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-[#0B0B0B] border border-accent hover:bg-accent-2 hover:border-accent-2 font-display font-bold",
  ghost:
    "bg-[var(--ghost-fill)] text-accent border border-[var(--ghost-border)] uppercase tracking-kicker font-mono font-semibold hover:bg-accent hover:text-[#0B0B0B] hover:border-accent",
  neutral:
    "bg-transparent text-body-2 border border-line font-mono uppercase tracking-kicker hover:text-ink hover:border-[rgba(245,243,238,.3)]",
  danger:
    "bg-transparent text-danger border border-[rgba(255,125,110,.4)] font-mono uppercase tracking-kicker hover:bg-danger hover:text-[#0B0B0B] hover:border-danger",
};

const SIZES: Record<Size, string> = {
  sm: "px-3 py-1.5 text-[11px]",
  md: "px-4 py-2.5 text-[12px]",
};

export function Button({
  variant = "neutral",
  size = "md",
  icon,
  loading = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-button transition-colors duration-150",
        "disabled:cursor-not-allowed disabled:opacity-45",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
    >
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={clsx(
        "inline-block h-3 w-3 animate-spin rounded-full border border-current border-t-transparent",
        className,
      )}
    />
  );
}

/** Small segmented control used for range and view switches. */
export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="inline-flex rounded-pill border border-line bg-transparent p-0.5"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={clsx(
            "rounded-pill px-3 py-1 font-mono text-[11px] uppercase tracking-kicker transition-colors",
            value === option.value
              ? "bg-[var(--ghost-fill)] text-accent"
              : "text-meta hover:text-ink",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
