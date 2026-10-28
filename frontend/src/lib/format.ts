/** Display helpers. Everything the UI prints goes through here. */

export function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  // The API returns rates as 0..100 already, not 0..1.
  return `${value.toFixed(digits)}%`;
}

export function ratio(numerator: number, denominator: number): number {
  if (!denominator) return 0;
  return (numerator / denominator) * 100;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function dateOnly(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** "42m ago", "3h ago", "in 2d" — deliberately coarse. */
export function relative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const deltaSeconds = Math.round((then - now.getTime()) / 1000);
  const future = deltaSeconds > 0;
  const abs = Math.abs(deltaSeconds);

  let text: string;
  if (abs < 60) text = `${abs}s`;
  else if (abs < 3600) text = `${Math.round(abs / 60)}m`;
  else if (abs < 86400) text = `${Math.round(abs / 3600)}h`;
  else text = `${Math.round(abs / 86400)}d`;

  return future ? `in ${text}` : `${text} ago`;
}

/** How stale a fetched payload is, in seconds. */
export function ageSeconds(iso: string | null | undefined): number {
  if (!iso) return Number.POSITIVE_INFINITY;
  return (Date.now() - new Date(iso).getTime()) / 1000;
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function initials(first: string, last: string, email?: string | null): string {
  const a = first.trim()[0] ?? email?.trim()[0] ?? "?";
  const b = last.trim()[0] ?? "";
  return `${a}${b}`.toUpperCase();
}
