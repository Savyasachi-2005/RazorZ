import { Link } from "react-router-dom";
import { EmptyState, ErrorBanner, PageSkeleton } from "../components/Status";
import { Button } from "../components/ui/Button";
import { HealthBar } from "../components/ui/HealthBar";
import { IconInbox } from "../components/ui/Icons";
import { KpiCard, SectionCard } from "../components/ui/KpiCard";
import { SyncBanner } from "../components/ui/LoadingFeedback";
import { PageHeader } from "../components/ui/PageHeader";
import { labelize, pct } from "../lib/format";
import { useAppData } from "../state/AppDataContext";

export function OverviewPage() {
  const {
    summary,
    audit,
    error,
    notice,
    busy,
    initialLoading,
    refreshing,
    clearError,
    generate,
    refresh,
  } = useAppData();

  const unmatched = summary
    ? summary.exceptions + summary.review_required + summary.unresolved
    : 0;
  const openExceptions = summary?.exceptions ?? 0;
  const showSkeleton = initialLoading && !summary;

  return (
    <section>
      <PageHeader
        title="Books close"
        subtitle="Deterministic match first. Exceptions stay open until a human decides."
        actions={
          <Button onClick={() => void generate(50)} loading={busy}>
            {busy ? "Running batch…" : "Run 50-record batch"}
          </Button>
        }
      />

      {error ? (
        <div className="mb-4">
          <ErrorBanner message={error} onRetry={() => { clearError(); void refresh(); }} />
        </div>
      ) : null}
      <SyncBanner
        show={!showSkeleton && (refreshing || busy)}
        message={busy ? "Running reconciliation batch…" : "Refreshing overview metrics…"}
      />
      {notice ? (
        <p className="mb-4 rounded-md border border-accent/30 bg-accent-soft/40 px-3 py-2 text-sm text-accent-text">
          {notice}
        </p>
      ) : null}

      {showSkeleton ? <PageSkeleton /> : null}

      {!showSkeleton && summary && summary.total_records === 0 ? (
        <EmptyState
          icon={<IconInbox />}
          title="No reconciliation runs yet"
          body="Your ledger is waiting for its first reconciliation batch. Generate a 50-record synthetic set to populate match rate, exceptions, and audit."
          action={
            <Button onClick={() => void generate(50)} loading={busy}>
              {busy ? "Running batch…" : "Run 50-record batch"}
            </Button>
          }
        />
      ) : null}

      {!showSkeleton && summary && summary.total_records > 0 ? (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <KpiCard label="Total processed" value={String(summary.total_records)} />
            <KpiCard label="Match rate" value={pct(summary.match_rate)} tone="good" />
            <KpiCard label="Matched" value={String(summary.matched)} tone="good" />
            <KpiCard
              label="Open breaks"
              value={String(unmatched)}
              tone={unmatched > 0 ? "bad" : "good"}
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <KpiCard label="Exceptions" value={String(summary.exceptions)} tone="bad" dense />
            <KpiCard label="Human review" value={String(summary.review_required)} tone="warn" dense />
            <KpiCard label="Unresolved" value={String(summary.unresolved)} tone="info" dense />
          </div>

          <div className="grid gap-5 lg:grid-cols-5">
            <SectionCard
              className="lg:col-span-3"
              title="Reconciliation health"
              subtitle="Composition of the latest ledger population"
            >
              <HealthBar summary={summary} />
            </SectionCard>

            <SectionCard
              className="lg:col-span-2"
              title="Action required"
              subtitle="What needs a human next"
            >
              {openExceptions > 0 || summary.review_required > 0 ? (
                <div>
                  <p className="text-sm text-ink">
                    <span className="font-mono text-xl font-semibold tabular-nums text-danger-text">
                      {openExceptions + summary.review_required}
                    </span>{" "}
                    items need attention
                  </p>
                  <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                    {openExceptions} exception rows · {summary.review_required} human-review matches.
                    Arithmetic stays locked until a reviewer acts.
                  </p>
                  <Link
                    to="/exceptions"
                    className="mt-4 inline-flex text-sm font-medium text-accent-text hover:underline"
                  >
                    Review exceptions →
                  </Link>
                </div>
              ) : (
                <p className="text-sm text-accent-text">All reconciliation breaks are resolved.</p>
              )}
            </SectionCard>
          </div>

          <div className="grid gap-5 lg:grid-cols-5">
            <SectionCard
              className="lg:col-span-3"
              title="Recent activity"
              subtitle="Latest engine and human events from the audit trail"
              action={
                <Link to="/audit" className="text-xs font-medium text-accent-text hover:underline">
                  View all
                </Link>
              }
            >
              {audit.length === 0 ? (
                <p className="text-sm text-ink-muted">No audit events yet for this environment.</p>
              ) : (
                <ul className="space-y-0 divide-y divide-line">
                  {audit.slice(0, 8).map((row) => (
                    <li key={row.id} className="flex gap-3 py-2.5 first:pt-0 last:pb-0">
                      <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-line-strong" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-ink">
                          <span className="font-medium">{labelize(row.action)}</span>
                          <span className="text-ink-muted"> · {labelize(row.event_type)}</span>
                        </p>
                        <p className="mt-0.5 truncate font-mono text-xs text-ink-faint">
                          #{row.id} · {row.actor} · {row.entity_id}
                          {row.new_state ? ` → ${row.new_state}` : ""}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>

            <SectionCard className="lg:col-span-2" title="System status" subtitle="Source and intelligence readiness">
              <ul className="space-y-3">
                <StatusRow name="Synthetic data" state="Connected" tone="good" detail="Demo ingestion path" />
                <StatusRow
                  name="Razorpay Test Mode"
                  state="Ready"
                  tone="good"
                  detail="Adapter live · sync from Data sources · engine stays independent"
                />
                <StatusRow
                  name="AI exception intelligence"
                  state="Ready"
                  tone="info"
                  detail="Gemini provider supported · mock default · advisory assist only"
                />
              </ul>
            </SectionCard>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function StatusRow({
  name,
  state,
  detail,
  tone,
}: {
  name: string;
  state: string;
  detail: string;
  tone: "good" | "warn" | "info";
}) {
  const color =
    tone === "good" ? "text-accent-text" : tone === "warn" ? "text-warn-text" : "text-info-text";
  return (
    <li className="rounded-md border border-line bg-surface-raised/50 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-ink">{name}</p>
        <span className={`text-xs font-semibold uppercase tracking-wide ${color}`}>{state}</span>
      </div>
      <p className="mt-1 text-xs text-ink-muted">{detail}</p>
    </li>
  );
}
