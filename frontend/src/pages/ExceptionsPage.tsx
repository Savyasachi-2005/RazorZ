import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ConfidenceBadge,
  EmptyState,
  ErrorBanner,
  PageSkeleton,
  SeverityBadge,
  StatusBadge,
} from "../components/Status";
import { Button } from "../components/ui/Button";
import { IconInbox } from "../components/ui/Icons";
import { KpiCard } from "../components/ui/KpiCard";
import { SyncBanner } from "../components/ui/LoadingFeedback";
import { PageHeader } from "../components/ui/PageHeader";
import { labelize, money } from "../lib/format";
import { useAppData } from "../state/AppDataContext";

export function ExceptionsPage() {
  const { exceptions, error, busy, initialLoading, refreshing, clearError, generate, refresh } = useAppData();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const recordParam = searchParams.get("record") ?? "";

  const [status, setStatus] = useState("ALL");
  const [type, setType] = useState("ALL");
  const [severity, setSeverity] = useState("ALL");
  const [query, setQuery] = useState(recordParam);

  useEffect(() => {
    if (recordParam) setQuery(recordParam);
  }, [recordParam]);

  // Deep-link fallback: if ?record= uniquely matches one exception, open its detail.
  useEffect(() => {
    if (!recordParam || initialLoading) return;
    const needle = recordParam.toLowerCase();
    const hits = exceptions.filter((row) => {
      const hay = `${row.description} ${row.evidence ?? ""}`.toLowerCase();
      return hay.includes(needle);
    });
    if (hits.length === 1) {
      navigate(`/exceptions/${hits[0].id}`, { replace: true });
    }
  }, [recordParam, exceptions, initialLoading, navigate]);

  const types = useMemo(
    () => ["ALL", ...Array.from(new Set(exceptions.map((i) => i.exception_type))).sort()],
    [exceptions],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return exceptions.filter((row) => {
      if (status !== "ALL" && row.status !== status) return false;
      if (type !== "ALL" && row.exception_type !== type) return false;
      if (severity !== "ALL" && row.severity.toLowerCase() !== severity.toLowerCase()) return false;
      if (!q) return true;
      return (
        String(row.id).includes(q) ||
        row.exception_type.toLowerCase().includes(q) ||
        row.description.toLowerCase().includes(q) ||
        (row.evidence ?? "").toLowerCase().includes(q)
      );
    });
  }, [exceptions, status, type, severity, query]);

  const open = exceptions.filter((i) => i.status === "OPEN").length;
  const resolved = exceptions.filter((i) => i.status === "RESOLVED").length;
  const rejected = exceptions.filter((i) => i.status === "REJECTED").length;
  const showSkeleton = initialLoading && exceptions.length === 0;

  return (
    <section>
      <PageHeader
        title="Exception queue"
        subtitle="Breaks that the engine would not auto-post."
      />

      {error ? (
        <div className="mb-4">
          <ErrorBanner message={error} onRetry={() => { clearError(); void refresh(); }} />
        </div>
      ) : null}

      <SyncBanner
        show={!showSkeleton && (refreshing || busy)}
        message={busy ? "Updating exception review…" : "Refreshing exception queue…"}
      />

      {!showSkeleton ? (
        <div className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <KpiCard label="Open exceptions" value={String(open)} tone={open ? "bad" : "good"} dense />
          <KpiCard label="Resolved" value={String(resolved)} tone="good" dense />
          <KpiCard label="Rejected" value={String(rejected)} tone="warn" dense />
        </div>
      ) : null}

      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center">
        <FilterSelect label="Status" value={status} onChange={setStatus} options={["ALL", "OPEN", "RESOLVED", "REJECTED"]} />
        <FilterSelect label="Type" value={type} onChange={setType} options={types} />
        <FilterSelect label="Severity" value={severity} onChange={setSeverity} options={["ALL", "high", "medium", "low"]} />
        <input
          type="search"
          placeholder="Search exceptions…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-ink-faint focus-ring lg:max-w-xs"
        />
      </div>

      {showSkeleton ? <PageSkeleton rows={5} /> : null}

      {!showSkeleton && exceptions.length === 0 && !error ? (
        <EmptyState
          icon={<IconInbox />}
          title="No exceptions in the queue"
          body="Either the book is clean, or no batch has been run yet. Run a reconciliation batch to surface breaks."
          action={
            <Button onClick={() => void generate(50)} loading={busy}>
              {busy ? "Running batch…" : "Run 50-record batch"}
            </Button>
          }
        />
      ) : null}

      {!showSkeleton && exceptions.length > 0 && filtered.length === 0 ? (
        <EmptyState title="No matching exceptions" body="Adjust filters or clear search to see the full queue." />
      ) : null}

      {!showSkeleton && filtered.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-line bg-surface/80 shadow-panel">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-line bg-surface-raised text-[11px] uppercase tracking-[0.06em] text-ink-faint">
              <tr>
                <th className="px-3 py-2.5 font-medium">ID</th>
                <th className="px-3 py-2.5 font-medium">Type</th>
                <th className="px-3 py-2.5 text-right font-medium">Amount</th>
                <th className="px-3 py-2.5 font-medium">Confidence</th>
                <th className="px-3 py-2.5 font-medium">Severity</th>
                <th className="px-3 py-2.5 font-medium">Analysis</th>
                <th className="px-3 py-2.5 font-medium">Status</th>
                <th className="px-3 py-2.5 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr
                  key={row.id}
                  className={`border-t border-line/80 hover:bg-surface-hover/50 ${
                    row.status === "OPEN" ? "bg-rose-950/10" : ""
                  }`}
                >
                  <td className="px-3 py-2.5">
                    <Link className="font-mono text-xs text-accent-text hover:underline" to={`/exceptions/${row.id}`}>
                      EX-{row.id}
                    </Link>
                  </td>
                  <td className="px-3 py-2.5">
                    <p className="text-sm text-ink">{labelize(row.exception_type)}</p>
                    <p className="mt-0.5 font-mono text-[11px] text-ink-faint">{row.priority}</p>
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-xs tabular-nums">{money(row.amount)}</td>
                  <td className="px-3 py-2.5">
                    <ConfidenceBadge value={row.confidence} />
                  </td>
                  <td className="px-3 py-2.5">
                    <SeverityBadge severity={row.severity} />
                  </td>
                  <td className="max-w-[220px] px-3 py-2.5 text-xs text-ink-muted">
                    <p className="line-clamp-2">{row.recommended_action ?? row.description}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusBadge status={row.status} showMark />
                  </td>
                  <td className="px-3 py-2.5">
                    <Link to={`/exceptions/${row.id}`} className="text-xs font-medium text-accent-text hover:underline">
                      Investigate
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-ink-muted">
      {label}
      <select
        className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink focus-ring"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </label>
  );
}
