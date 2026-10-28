import clsx from "clsx";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

/** Every form field in the app uses the deck's `.sign-field` treatment. */
const FIELD = [
  "w-full rounded-input border border-line bg-[var(--field-fill)] px-3 py-2",
  "font-mono text-[13px] text-ink placeholder:text-meta",
  "transition-colors duration-150",
  "focus:border-accent focus:outline-none",
  "disabled:cursor-not-allowed disabled:opacity-50",
].join(" ");

export function Field({
  label,
  hint,
  error,
  htmlFor,
  className,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className={clsx("flex min-w-0 flex-col gap-1.5", className)}>
      <span className="font-mono text-[11px] uppercase tracking-kicker text-meta">{label}</span>
      {children}
      {hint && !error && <span className="font-mono text-[11px] text-meta-2">{hint}</span>}
      {error && (
        <span role="alert" className="font-mono text-[11px] text-danger">
          {error}
        </span>
      )}
    </label>
  );
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={clsx(FIELD, className)} />;
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={clsx(FIELD, "min-h-24 resize-y leading-relaxed", className)} />;
}

export function Select({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <select {...rest} className={clsx(FIELD, "appearance-none pr-8", className)}>
      {children}
    </select>
  );
}
