import type { ReactNode } from "react";

export function SyncBanner({
  show,
  message = "Refreshing latest data…",
}: {
  show: boolean;
  message?: string;
}) {
  if (!show) return null;
  return (
    <div
      className="mb-4 flex items-center gap-2 rounded-md border border-info/30 bg-info-soft/40 px-3 py-2 text-sm text-info-text"
      role="status"
      aria-live="polite"
    >
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-info/30 border-t-info-text" />
      <span>{message}</span>
    </div>
  );
}

export function InlineSpinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-ink-muted">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-line-strong border-t-accent" />
      {label ? <span>{label}</span> : null}
    </span>
  );
}

export function LoadingOverlay({
  show,
  children,
  label = "Loading…",
}: {
  show: boolean;
  children: ReactNode;
  label?: string;
}) {
  return (
    <div className="relative">
      <div className={show ? "pointer-events-none opacity-60 transition-opacity" : ""}>{children}</div>
      {show ? (
        <div className="absolute inset-0 z-10 flex items-start justify-center bg-canvas/20 pt-16 backdrop-blur-[1px]">
          <div className="flex items-center gap-2 rounded-lg border border-line bg-surface px-4 py-2.5 text-sm text-ink shadow-panel">
            <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
            {label}
          </div>
        </div>
      ) : null}
    </div>
  );
}
