import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState, ErrorBanner, PageSkeleton, StatusBadge, ConfidenceBadge } from "../components/Status";
import { Button } from "../components/ui/Button";
import { IconInbox } from "../components/ui/Icons";
import { SyncBanner } from "../components/ui/LoadingFeedback";
import { PageHeader } from "../components/ui/PageHeader";
import { reviewPathForRecord } from "../lib/exceptionLink";
import { money, pairTypeLabel, splitPair } from "../lib/format";
import { useAppData } from "../state/AppDataContext";

const PAIR_FILTERS = [
  { value: "ALL", label: "All types" },
  { value: "order_payment", label: "Order ↔ Payment" },
  { value: "payment_settlement", label: "Payment ↔ Settlement" },
  { value: "payment_refund", label: "Payment ↔ Refund" },
  { value: "payment_fee", label: "Payment ↔ Fee" },
] as const;

export function ReconciliationPage() {
  const { records, exceptions, error, busy, initialLoading, refreshing, clearError, generate, refresh } = useAppData();
  const [status, setStatus] = useState("ALL");
  const [pairType, setPairType] = useState("ALL");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return records.filter((row) => {
      if (status !== "ALL" && row.status !== status) return false;
      if (pairType !== "ALL" && (row.pair_type ?? "order_payment") !== pairType) return false;
      if (!q) return true;
      const hay = [
        row.record_id,
        row.matched_with,
        row.run_id,
        row.order_id,
        row.payment_id,
        row.settlement_id,
        row.refund_id,
        row.fee_id,
        row.exception_type,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [records, status, pairType, query]);

  const showSkeleton = initialLoading && records.length === 0;

  return (
    <section>
      <PageHeader
        title="Reconciliation ledger"
        subtitle="Multi-record matches across order, payment, settlement, refund, and fee — with confidence and residual amount."
        actions={
          <Button variant="secondary" onClick={() => void generate(50)} loading={busy}>
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
        message={busy ? "Running reconciliation batch…" : "Refreshing ledger…"}
      />

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <label className="flex items-center gap-2 text-sm text-ink-muted">
          Status
          <select
            className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink focus-ring"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            {["ALL", "MATCHED", "AUTO_RESOLVED", "REVIEW_REQUIRED", "EXCEPTION", "UNRESOLVED"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-ink-muted">
          Record type
          <select
            className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink focus-ring"
            value={pairType}
            onChange={(event) => setPairType(event.target.value)}
          >
            {PAIR_FILTERS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <input
          type="search"
          placeholder="Search transaction ID…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-ink-faint focus-ring sm:max-w-xs"
        />
        <p className="text-xs text-ink-faint sm:ml-auto">{filtered.length} rows</p>
      </div>

      <p className="mb-3 text-xs text-ink-muted">
        Each row is one relationship. Order↔Payment rows leave Settlement/Refund/Fee blank by design —
        switch <span className="text-ink">Record type</span> to Payment↔Settlement, Payment↔Refund, or Payment↔Fee.
      </p>

      {showSkeleton ? <PageSkeleton rows={6} /> : null}

      {!showSkeleton && filtered.length === 0 && !error ? (
        <EmptyState
          icon={<IconInbox />}
          title="No reconciliation data yet"
          body="Run a reconciliation batch to populate the ledger with multi-record pairs and confidence scores."
          action={
            <Button onClick={() => void generate(50)} loading={busy}>
              {busy ? "Running batch…" : "Run 50-record batch"}
            </Button>
          }
        />
      ) : null}

      {!showSkeleton && filtered.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-line bg-surface/80 shadow-panel">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-line bg-surface-raised text-[11px] uppercase tracking-[0.06em] text-ink-faint">
              <tr>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Status</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Type</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Order</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Payment</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Settlement</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Refund</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Fee</th>
                <th className="whitespace-nowrap px-3 py-2.5 text-right font-medium">Difference</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Confidence</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Reason</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => {
                const pair = splitPair(row.record_id, row.matched_with, {
                  pair_type: row.pair_type,
                  order_id: row.order_id,
                  payment_id: row.payment_id,
                  settlement_id: row.settlement_id,
                  refund_id: row.refund_id,
                  fee_id: row.fee_id,
                });
                const needsReview = row.status === "EXCEPTION" || row.status === "REVIEW_REQUIRED";
                const reviewTo = needsReview ? reviewPathForRecord(row, exceptions) : null;
                return (
                  <tr key={row.id} className="border-t border-line/80 hover:bg-surface-hover/50">
                    <td className="px-3 py-2">
                      <StatusBadge status={row.status} showMark />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs text-ink-muted">
                      {pairTypeLabel(row.pair_type)}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs tabular-nums text-ink">{pair.orderId}</td>
                    <td className="px-3 py-2 font-mono text-xs tabular-nums text-ink-muted">{pair.paymentId}</td>
                    <td className="px-3 py-2 font-mono text-xs tabular-nums text-ink-muted">{pair.settlementId}</td>
                    <td className="px-3 py-2 font-mono text-xs tabular-nums text-ink-muted">{pair.refundId}</td>
                    <td className="px-3 py-2 font-mono text-xs tabular-nums text-ink-muted">{pair.feeId}</td>
                    <td className="px-3 py-2 text-right font-mono text-xs tabular-nums text-ink">
                      {money(row.amount_diff)}
                    </td>
                    <td className="px-3 py-2">
                      <ConfidenceBadge value={row.confidence} />
                    </td>
                    <td className="max-w-[160px] truncate px-3 py-2 text-xs text-ink-muted">
                      {row.exception_type ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      {reviewTo ? (
                        <Link to={reviewTo} className="text-xs font-medium text-accent-text hover:underline">
                          Review
                        </Link>
                      ) : (
                        <span className="text-xs text-ink-faint">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
