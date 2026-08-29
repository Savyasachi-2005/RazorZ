import { useEffect, useState, type ComponentType, type SVGProps } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAppData } from "../state/AppDataContext";
import { useAuth } from "../state/AuthContext";
import { Button } from "../components/ui/Button";
import { GlobalBusyIndicator } from "../components/ui/GlobalBusyIndicator";
import {
  IconAlert,
  IconAudit,
  IconClose,
  IconCopilot,
  IconLedger,
  IconMenu,
  IconOverview,
  IconSignOut,
  IconSources,
} from "../components/ui/Icons";
import { InlineSpinner } from "../components/ui/LoadingFeedback";

const links: {
  to: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}[] = [
  { to: "/", label: "Overview", icon: IconOverview },
  { to: "/reconciliation", label: "Reconciliation", icon: IconLedger },
  { to: "/exceptions", label: "Exceptions", icon: IconAlert },
  { to: "/copilot", label: "Finance Copilot", icon: IconCopilot },
  { to: "/audit", label: "Audit trail", icon: IconAudit },
  { to: "/sources", label: "Data sources", icon: IconSources },
];

export function AppShell() {
  const [open, setOpen] = useState(false);
  const { healthStatus, refreshing, busy, busyLabel, initialLoading, refresh } = useAppData();
  const { user, signOut, signingOut } = useAuth();

  useEffect(() => {
    setOpen(false);
  }, []);

  return (
    <div className="min-h-screen text-ink">
      <GlobalBusyIndicator />
      <div className="flex min-h-screen">
        {open ? (
          <button
            type="button"
            aria-label="Close navigation"
            className="fixed inset-0 z-30 bg-black/50 lg:hidden"
            onClick={() => setOpen(false)}
          />
        ) : null}

        <aside
          className={`fixed inset-y-0 left-0 z-40 flex w-[240px] flex-col border-r border-line bg-canvas/95 backdrop-blur-md transition-transform duration-fast lg:translate-x-0 ${
            open ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <div className="shrink-0 border-b border-line px-5 py-5">
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="font-mono text-[11px] font-semibold tracking-[0.28em] text-accent-text">RAZORZ</p>
                <h1 className="mt-2 text-lg font-semibold tracking-tight text-ink">Finance Controller</h1>
                <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                  Deterministic books.
                  <br />
                  Human review for breaks.
                </p>
              </div>
              <button
                type="button"
                className="rounded-md p-1 text-ink-muted hover:bg-surface-hover hover:text-ink lg:hidden"
                onClick={() => setOpen(false)}
                aria-label="Close menu"
              >
                <IconClose />
              </button>
            </div>
          </div>

          <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3 py-4" aria-label="Primary">
            <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
              Operations
            </p>
            {links.map((link) => {
              const Icon = link.icon;
              return (
                <NavLink
                  key={link.to}
                  to={link.to}
                  end={link.to === "/"}
                  onClick={() => setOpen(false)}
                  className={({ isActive }) =>
                    `group flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors duration-fast focus-ring ${
                      isActive
                        ? "bg-surface-raised text-ink shadow-[inset_2px_0_0_0_#16a34a]"
                        : "text-ink-muted hover:bg-surface-hover hover:text-ink"
                    }`
                  }
                >
                  <Icon className="opacity-80" />
                  {link.label}
                </NavLink>
              );
            })}
          </nav>

          <div className="mt-auto shrink-0 border-t border-line px-4 py-4">
            {user ? (
              <div className="mb-3 rounded-md border border-line bg-surface/70 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-raised text-[11px] font-semibold uppercase text-accent-text"
                  >
                    {(user.full_name || user.email).trim().charAt(0)}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-ink" title={user.email}>
                      {user.full_name || user.email}
                    </p>
                    <p className="truncate text-[10px] text-ink-faint" title={user.email}>
                      {user.role}
                    </p>
                  </div>
                </div>
                <Button
                  variant="secondary"
                  className="mt-2.5 w-full !py-1.5 text-xs"
                  loading={signingOut}
                  onClick={() => void signOut()}
                >
                  {signingOut ? "Signing out…" : (
                    <>
                      <IconSignOut width={14} height={14} />
                      Sign out
                    </>
                  )}
                </Button>
              </div>
            ) : null}
            <div className="rounded-md border border-line bg-surface/70 px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-faint">System</p>
                <button
                  type="button"
                  className="text-[10px] font-medium text-accent-text hover:underline disabled:opacity-50"
                  disabled={refreshing || busy || initialLoading}
                  onClick={() => void refresh()}
                >
                  Refresh
                </button>
              </div>
              <div className="mt-2 flex items-center gap-2 text-xs">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    busy || refreshing || initialLoading
                      ? "animate-pulse bg-info"
                      : healthStatus === "ok"
                        ? "bg-accent"
                        : healthStatus === "degraded"
                          ? "bg-warn"
                          : "bg-ink-faint"
                  }`}
                />
                <span className="text-ink-muted">
                  {busy
                    ? busyLabel || "Working…"
                    : initialLoading
                      ? "Loading…"
                      : refreshing
                        ? "Syncing…"
                        : healthStatus === "ok"
                          ? "API healthy"
                          : healthStatus === "degraded"
                            ? "API degraded"
                            : "Checking…"}
                </span>
              </div>
              {(busy || refreshing || initialLoading) && (
                <div className="mt-2">
                  <InlineSpinner label="Please wait — request in progress" />
                </div>
              )}
              <p className="mt-1.5 font-mono text-[10px] text-ink-faint">RAZORZ UI · v0.1.0</p>
            </div>
          </div>
        </aside>

        {/* The sidebar is fixed at every breakpoint, so reserve its width here. */}
        <div className="flex min-w-0 flex-1 flex-col lg:pl-[240px]">
          <div className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-canvas/80 px-4 py-3 backdrop-blur-md lg:hidden">
            <Button variant="ghost" className="!px-2" onClick={() => setOpen(true)} aria-label="Open menu">
              <IconMenu />
            </Button>
            <p className="font-mono text-xs tracking-[0.2em] text-accent-text">RAZORZ</p>
            {(busy || refreshing) && <InlineSpinner label={busy ? "Working" : "Syncing"} />}
            {user ? (
              <Button
                variant="ghost"
                className="ml-auto !px-2"
                loading={signingOut}
                onClick={() => void signOut()}
                aria-label="Sign out"
                title="Sign out"
              >
                {signingOut ? null : <IconSignOut width={16} height={16} />}
              </Button>
            ) : null}
          </div>
          <main className="mx-auto w-full max-w-shell flex-1 px-4 py-5 sm:px-6 lg:px-8 lg:py-7">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
