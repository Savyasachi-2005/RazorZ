import { useCallback, useEffect, useState } from "react";
import { ErrorBanner } from "../components/Status";
import { Button } from "../components/ui/Button";
import { SectionCard } from "../components/ui/KpiCard";
import { SyncBanner } from "../components/ui/LoadingFeedback";
import { PageHeader } from "../components/ui/PageHeader";
import { StatusBadge } from "../components/ui/Badges";
import { api, type RazorpayStatus } from "../api";
import { useAppData } from "../state/AppDataContext";

export function SourcesPage() {
  const { summary, healthStatus, error, busy, refreshing, clearError, generate, refresh } = useAppData();
  const [rzp, setRzp] = useState<RazorpayStatus | null>(null);
  const [rzpBusy, setRzpBusy] = useState(false);
  const [rzpMessage, setRzpMessage] = useState("");
  const [rzpError, setRzpError] = useState("");

  const loadRazorpay = useCallback(async () => {
    try {
      const status = await api.razorpayStatus();
      setRzp(status);
      setRzpError("");
    } catch (err) {
      setRzpError(err instanceof Error ? err.message : "Could not load Razorpay status");
    }
  }, []);

  useEffect(() => {
    void loadRazorpay();
  }, [loadRazorpay]);

  const syncRazorpay = async () => {
    setRzpBusy(true);
    setRzpError("");
    setRzpMessage("");
    try {
      const result = await api.razorpaySync(50);
      setRzpMessage(result.message);
      await Promise.all([loadRazorpay(), refresh({ silent: true })]);
    } catch (err) {
      setRzpError(err instanceof Error ? err.message : "Razorpay sync failed");
    } finally {
      setRzpBusy(false);
    }
  };

  const rzpBadge = !rzp
    ? "…"
    : !rzp.configured
      ? "NOT CONFIGURED"
      : rzp.connected
        ? "CONNECTED"
        : "ERROR";

  return (
    <section>
      <PageHeader
        title="Data sources"
        subtitle="Connect financial sources and normalize them into the RAZORZ ledger."
      />

      {error ? (
        <div className="mb-4">
          <ErrorBanner message={error} onRetry={() => { clearError(); void refresh(); }} />
        </div>
      ) : null}
      {rzpError ? (
        <div className="mb-4">
          <ErrorBanner message={rzpError} onRetry={() => { setRzpError(""); void loadRazorpay(); }} />
        </div>
      ) : null}

      <SyncBanner
        show={refreshing || busy || rzpBusy}
        message={
          rzpBusy
            ? "Syncing Razorpay Test Mode…"
            : busy
              ? "Running synthetic batch…"
              : "Refreshing source status…"
        }
      />

      {rzpMessage ? (
        <p className="mb-4 rounded-md border border-accent/30 bg-accent-soft/40 px-3 py-2 text-sm text-accent-text">
          {rzpMessage}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <article className="rounded-lg border border-line bg-surface/80 p-4 shadow-panel">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-faint">Synthetic data</p>
              <h3 className="mt-1 text-lg font-semibold text-ink">Demo generator</h3>
            </div>
            <StatusBadge status="CONNECTED" />
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-ink-faint">Records in ledger</dt>
              <dd className="mt-1 font-mono text-xl tabular-nums text-ink">
                {summary ? summary.total_records : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-ink-faint">API</dt>
              <dd className="mt-1 text-sm text-ink">
                {healthStatus === "ok" ? "Healthy" : healthStatus === "degraded" ? "Degraded" : "…"}
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-sm leading-relaxed text-ink-muted">
            Seeded batches with exact matches, amount breaks, and missing payments for evaluation and demos.
          </p>
          <div className="mt-4">
            <Button onClick={() => void generate(50)} loading={busy}>
              {busy ? "Running batch…" : "Run batch"}
            </Button>
          </div>
        </article>

        <article className="rounded-lg border border-line bg-surface/80 p-4 shadow-panel">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-faint">Razorpay test mode</p>
              <h3 className="mt-1 text-lg font-semibold text-ink">Payment adapter</h3>
            </div>
            <StatusBadge status={rzpBadge} />
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-ink-faint">Environment</dt>
              <dd className="mt-1 text-sm text-ink">{rzp?.mode ?? "test"}</dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-ink-faint">Key</dt>
              <dd className="mt-1 font-mono text-xs text-ink-muted">{rzp?.key_id_prefix ?? "—"}</dd>
            </div>
          </dl>
          <p className="mt-3 text-sm leading-relaxed text-ink-muted">
            Fetches orders, payments, refunds, and settlements (partial in test), normalizes them, then runs the
            same deterministic engine. Settlements are not fabricated when Razorpay returns none.
          </p>
          {rzp?.error ? (
            <p className="mt-2 text-xs text-danger">{rzp.error.message}</p>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() => void syncRazorpay()}
              loading={rzpBusy}
              disabled={!rzp?.configured}
            >
              {rzpBusy ? "Syncing…" : "Sync & reconcile"}
            </Button>
            <Button variant="ghost" onClick={() => void loadRazorpay()} disabled={rzpBusy}>
              Refresh status
            </Button>
          </div>
        </article>
      </div>

      <SectionCard
        className="mt-5"
        title="Data flow"
        subtitle="How records move from source into books"
      >
        <ol className="flex flex-col gap-0 sm:flex-row sm:flex-wrap sm:items-center sm:gap-2">
          {[
            "Source",
            "Normalizer",
            "Reconciliation engine",
            "Exception intelligence",
            "Audit trail",
          ].map((step, index, arr) => (
            <li key={step} className="flex items-center gap-2">
              <span className="rounded-md border border-line bg-surface-raised px-3 py-2 text-sm font-medium text-ink">
                {step}
              </span>
              {index < arr.length - 1 ? (
                <span className="hidden text-ink-faint sm:inline" aria-hidden>
                  →
                </span>
              ) : null}
              {index < arr.length - 1 ? (
                <span className="text-ink-faint sm:hidden" aria-hidden>
                  ↓
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      </SectionCard>
    </section>
  );
}
