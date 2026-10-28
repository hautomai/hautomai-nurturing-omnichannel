import clsx from "clsx";
import { useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useWorkspace } from "../../app/WorkspaceContext";
import { useMeta, useOverview, useSetHalt } from "../../lib/hooks";
import { num } from "../../lib/format";
import { Button } from "../ui/Button";
import { Banner, LiveDot, StatusPill } from "../ui/Indicators";
import { Modal } from "../ui/Feedback";

const NAV = [
  { to: "/", label: "Dashboard", glyph: "◎" },
  { to: "/sequences", label: "Sequences", glyph: "⇄" },
  { to: "/contacts", label: "Contacts", glyph: "☰" },
  { to: "/inboxes", label: "Inboxes", glyph: "✉" },
  { to: "/templates", label: "Templates", glyph: "❏" },
  { to: "/analytics", label: "Analytics", glyph: "◔" },
  { to: "/settings", label: "Settings", glyph: "⚙" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const { client, clientId } = useWorkspace();
  const { data: meta } = useMeta();
  const { data: overview, isError: overviewError, dataUpdatedAt } = useOverview(clientId, 30);
  const setHalt = useSetHalt(clientId ?? "");
  const [confirming, setConfirming] = useState(false);
  const location = useLocation();

  const halted = client?.sending_halted ?? false;

  // Freshness dot: accent under 90s, warm when stale, danger on fetch error.
  const ageSeconds = dataUpdatedAt ? (Date.now() - dataUpdatedAt) / 1000 : Number.POSITIVE_INFINITY;
  const freshness = overviewError
    ? { tone: "danger" as const, label: "Stale data" }
    : ageSeconds < 90
      ? { tone: "accent" as const, label: "Live" }
      : { tone: "warm" as const, label: "Catching up" };

  const pageTitle =
    NAV.find((item) => item.to !== "/" && location.pathname.startsWith(item.to))?.label ?? "Dashboard";

  return (
    <div className="relative flex min-h-screen">
      <div className="app-texture" aria-hidden />

      <aside
        className={clsx(
          "relative z-10 flex shrink-0 flex-col border-r border-line bg-[var(--panel-fill)] backdrop-blur transition-[width] duration-200",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-4">
          <span className="font-display text-[19px] font-bold tracking-display text-ink">
            Hautom
            {!collapsed && <em className="not-italic text-accent">ai</em>}
          </span>
          <button
            type="button"
            aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
            onClick={() => setCollapsed((value) => !value)}
            className="rounded-button border border-line px-2 py-1 font-mono text-[11px] text-meta transition-colors hover:text-ink"
          >
            {collapsed ? "»" : "«"}
          </button>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 p-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              title={item.label}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 rounded-input border-l-2 px-3 py-2.5 font-mono text-[12px] transition-colors",
                  isActive
                    ? "border-l-accent bg-panel-2 text-ink"
                    : "border-l-transparent text-meta hover:bg-[rgba(255,255,255,.03)] hover:text-ink",
                )
              }
            >
              <span aria-hidden className="w-4 text-center text-[13px]">
                {item.glyph}
              </span>
              {!collapsed && <span className="truncate">{item.label}</span>}
            </NavLink>
          ))}
        </nav>

        {!collapsed && (
          <div className="border-t border-line px-4 py-3">
            <p className="font-mono text-[10px] uppercase tracking-kicker text-meta">Channel mode</p>
            <p className="mt-1 font-mono text-[11px] text-body-2">
              {meta?.channel_mode ?? "…"} · crm {meta?.crm_mode ?? "…"}
            </p>
          </div>
        )}
      </aside>

      <div className="relative z-10 flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-[rgba(10,10,10,.6)] px-6 py-4 backdrop-blur">
          <div className="flex min-w-0 items-center gap-3">
            <h1 className="font-display text-[22px] font-bold tracking-title text-ink">{pageTitle}</h1>
            <span className="hidden font-mono text-[11px] text-meta-2 sm:inline">
              / {location.pathname.replace(/^\//, "") || "dashboard"}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <LiveDot tone={freshness.tone} label={freshness.label} />

            <select
              disabled
              value={client?.id ?? ""}
              aria-label="Client (single tenant in v1)"
              className="cursor-not-allowed rounded-input border border-line bg-[var(--field-fill)] px-3 py-1.5 font-mono text-[12px] text-body-2"
            >
              <option value={client?.id ?? ""}>{client?.name ?? "No client"}</option>
            </select>

            {overview && (
              <span className="tabular hidden font-mono text-[11px] text-meta md:inline">
                {num(overview.sent_today)}/{num(overview.daily_capacity)} today
              </span>
            )}

            {halted ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setHalt.mutate({ sending_halted: false })}
                loading={setHalt.isPending}
              >
                Resume sends
              </Button>
            ) : (
              <Button variant="danger" size="sm" onClick={() => setConfirming(true)}>
                Pause all sends
              </Button>
            )}
          </div>
        </header>

        {halted && (
          <div className="px-6 pt-4">
            <Banner
              tone="danger"
              title="All sending is halted"
              action={
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setHalt.mutate({ sending_halted: false })}
                  loading={setHalt.isPending}
                >
                  Lift halt
                </Button>
              }
            >
              {client?.halt_reason ?? "Kill switch engaged"} — every enrollment is paused at its next
              touch and will resume when the halt is lifted.
            </Banner>
          </div>
        )}

        <main className="min-w-0 flex-1 px-6 py-6">{children}</main>
      </div>

      <Modal
        open={confirming}
        title="Pause all sends?"
        onClose={() => setConfirming(false)}
        footer={
          <>
            <Button onClick={() => setConfirming(false)}>Cancel</Button>
            <Button
              variant="danger"
              loading={setHalt.isPending}
              onClick={() => {
                setHalt.mutate(
                  { sending_halted: true, halt_reason: "manually halted from the app" },
                  { onSuccess: () => setConfirming(false) },
                );
              }}
            >
              Pause everything
            </Button>
          </>
        }
      >
        <p className="font-mono text-[13px] leading-relaxed text-body-2">
          Every active enrollment pauses immediately at its next touch. Nothing already queued is
          sent. Nothing is lost — enrollments resume exactly where they stopped when you lift the
          halt.
        </p>
        <div className="mt-4 flex flex-col gap-2">
          <StatusPill tone="danger" label="Email + SMS + Slack alerts stopped" />
          <StatusPill tone="warm" label="In-flight timers are cancelled, not expired" />
        </div>
      </Modal>
    </div>
  );
}
