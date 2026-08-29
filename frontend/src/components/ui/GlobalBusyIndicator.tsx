import { useEffect, useState } from "react";
import { useAppData } from "../../state/AppDataContext";

/** Top progress strip + centered status pill while work is in flight. */
export function GlobalBusyIndicator() {
  const { busy, busyLabel, refreshing, initialLoading } = useAppData();
  const active = busy || refreshing || initialLoading;
  const [elapsed, setElapsed] = useState(0);
  const [visible, setVisible] = useState(false);

  // Avoid flicker for very short syncs; show only if work lasts >280ms.
  useEffect(() => {
    if (!active) {
      setVisible(false);
      setElapsed(0);
      return;
    }
    const showTimer = window.setTimeout(() => setVisible(true), 280);
    const started = Date.now();
    const tick = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 250);
    return () => {
      window.clearTimeout(showTimer);
      window.clearInterval(tick);
    };
  }, [active, busy, refreshing, initialLoading]);

  if (!active || !visible) {
    return active ? (
      <div
        className="pointer-events-none fixed inset-x-0 top-0 z-[70] h-0.5 overflow-hidden"
        aria-hidden
      >
        <div className="h-full w-1/3 animate-[loadslide_1.1s_ease-in-out_infinite] bg-accent shadow-[0_0_12px_rgba(22,163,74,0.55)]" />
      </div>
    ) : null;
  }

  const label = busy
    ? busyLabel || "Running operation…"
    : initialLoading
      ? "Loading workspace…"
      : "Syncing data…";

  return (
    <>
      <div
        className="pointer-events-none fixed inset-x-0 top-0 z-[70] h-0.5 overflow-hidden"
        aria-hidden
      >
        <div className="h-full w-1/3 animate-[loadslide_1.1s_ease-in-out_infinite] bg-accent shadow-[0_0_12px_rgba(22,163,74,0.55)]" />
      </div>

      <div
        className="pointer-events-none fixed inset-x-0 bottom-5 z-[70] flex justify-center px-4"
        role="status"
        aria-live="polite"
      >
        <div className="pointer-events-auto flex max-w-[min(100%,24rem)] items-center gap-2.5 rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm text-ink shadow-panel">
          <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
          <span className="min-w-0 truncate font-medium">{label}</span>
          <span className="shrink-0 font-mono text-xs tabular-nums text-ink-muted">
            {formatElapsed(elapsed)}
          </span>
        </div>
      </div>
    </>
  );
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `${s}s`;
}
