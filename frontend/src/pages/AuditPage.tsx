import { useEffect, useMemo, useState } from "react";
import type { AuditItem } from "../api";
import { EmptyState, ErrorBanner, PageSkeleton, StatusBadge } from "../components/Status";
import { Button } from "../components/ui/Button";
import { IconInbox } from "../components/ui/Icons";
import { KpiCard } from "../components/ui/KpiCard";
import { LoadingOverlay, SyncBanner } from "../components/ui/LoadingFeedback";
import { PageHeader } from "../components/ui/PageHeader";
import { conf, labelize } from "../lib/format";
import { useAppData } from "../state/AppDataContext";

const PAGE_SIZES = [25, 50, 100] as const;

type ViewMode = "table" | "timeline";

export function AuditPage() {
  const { audit, error, busy, initialLoading, refreshing, clearError, generate, refresh } = useAppData();
  const [actor, setActor] = useState("ALL");
  const [eventType, setEventType] = useState("ALL");
  const [action, setAction] = useState("ALL");
  const [query, setQuery] = useState("");
  const [pageSize, setPageSize] = useState<(typeof PAGE_SIZES)[number]>(25);
  const [page, setPage] = useState(1);
  const [view, setView] = useState<ViewMode>("table");

  const actors = useMemo(
    () => ["ALL", ...Array.from(new Set(audit.map((row) => row.actor))).sort()],
    [audit],
  );
  const eventTypes = useMemo(
    () => ["ALL", ...Array.from(new Set(audit.map((row) => row.event_type))).sort()],
    [audit],
  );
  const actions = useMemo(
    () => ["ALL", ...Array.from(new Set(audit.map((row) => row.action))).sort()],
    [audit],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return audit.filter((row) => {
      if (actor !== "ALL" && row.actor !== actor) return false;
      if (eventType !== "ALL" && row.event_type !== eventType) return false;
      if (action !== "ALL" && row.action !== action) return false;
      if (!q) return true;
      return (
        String(row.id).includes(q) ||
        row.actor.toLowerCase().includes(q) ||
        row.action.toLowerCase().includes(q) ||
        row.event_type.toLowerCase().includes(q) ||
        row.entity_id.toLowerCase().includes(q) ||
        (row.new_state ?? "").toLowerCase().includes(q)
      );
    });
  }, [audit, actor, eventType, action, query]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageRows = useMemo(() => {
    const start = (safePage - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, safePage, pageSize]);

  useEffect(() => {
    setPage(1);
  }, [actor, eventType, action, query, pageSize]);

  const humanCount = audit.filter((row) => isHuman(row.actor)).length;
  const systemCount = audit.filter((row) => !isHuman(row.actor)).length;
  const showSkeleton = initialLoading && audit.length === 0;
  const filtersActive = actor !== "ALL" || eventType !== "ALL" || action !== "ALL" || query.trim() !== "";

  function clearFilters() {
    setActor("ALL");
    setEventType("ALL");
    setAction("ALL");
    setQuery("");
  }

  return (
    <section>
      <PageHeader
        title="Audit trail"
        subtitle="Every engine decision and human review is append-only. Filter and page through events instead of scrolling one long list."
        actions={
          <div className="flex items-center gap-1 rounded-md border border-line bg-surface p-0.5">
            <ViewToggle active={view === "table"} onClick={() => setView("table")} label="Table" />
            <ViewToggle active={view === "timeline"} onClick={() => setView("timeline")} label="Timeline" />
          </div>
        }
      />

      {error ? (
        <div className="mb-4">
          <ErrorBanner
            message={error}
            onRetry={() => {
              clearError();
              void refresh();
            }}
          />
        </div>
      ) : null}

      <SyncBanner
        show={!initialLoading && (refreshing || busy)}
        message={busy ? "Running operation — audit will update when finished…" : "Refreshing audit events…"}
      />

      {showSkeleton ? <PageSkeleton rows={6} /> : null}

      {!showSkeleton && audit.length === 0 && !error ? (
        <EmptyState
          icon={<IconInbox />}
          title="No audit events yet"
          body="The trail stays empty until a reconciliation batch or human review writes an append-only event."
          action={
            <Button onClick={() => void generate(50)} loading={busy}>
              {busy ? "Running batch…" : "Run 50-record batch"}
            </Button>
          }
        />
      ) : null}

      {!showSkeleton && audit.length > 0 ? (
        <LoadingOverlay show={refreshing && !busy} label="Updating audit trail…">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <KpiCard label="Events loaded" value={String(audit.length)} dense />
            <KpiCard label="Showing" value={String(filtered.length)} tone="info" dense />
            <KpiCard label="System" value={String(systemCount)} tone="good" dense />
            <KpiCard label="Human" value={String(humanCount)} tone="warn" dense />
          </div>

          <div className="rounded-lg border border-line bg-surface/80 p-3 shadow-panel sm:p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-end">
              <FilterSelect label="Actor" value={actor} onChange={setActor} options={actors} />
              <FilterSelect label="Event" value={eventType} onChange={setEventType} options={eventTypes} />
              <FilterSelect label="Action" value={action} onChange={setAction} options={actions} />
              <label className="flex min-w-[200px] flex-1 flex-col gap-1 text-xs text-ink-muted">
                Search
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="ID, entity, actor, state…"
                  className="rounded-md border border-line bg-canvas px-3 py-1.5 text-sm text-ink placeholder:text-ink-faint focus-ring"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-ink-muted">
                Rows / page
                <select
                  className="rounded-md border border-line bg-canvas px-2.5 py-1.5 text-sm text-ink focus-ring"
                  value={pageSize}
                  onChange={(e) => setPageSize(Number(e.target.value) as (typeof PAGE_SIZES)[number])}
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
              </label>
              {filtersActive ? (
                <Button variant="secondary" onClick={clearFilters} className="lg:mb-0.5">
                  Clear filters
                </Button>
              ) : null}
            </div>
          </div>

          {filtered.length === 0 ? (
            <EmptyState
              title="No matching audit events"
              body="Try clearing filters or searching a different entity / actor."
              action={
                <Button variant="secondary" onClick={clearFilters}>
                  Clear filters
                </Button>
              }
            />
          ) : (
            <>
              {view === "table" ? <AuditTable rows={pageRows} /> : <AuditTimeline rows={pageRows} />}

              <div className="flex flex-col gap-3 rounded-lg border border-line bg-surface/60 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-ink-muted">
                  Page <span className="font-mono text-ink">{safePage}</span> of{" "}
                  <span className="font-mono text-ink">{totalPages}</span>
                  <span className="text-ink-faint">
                    {" "}
                    · {(safePage - 1) * pageSize + 1}–{Math.min(safePage * pageSize, filtered.length)} of{" "}
                    {filtered.length}
                  </span>
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    disabled={safePage <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    Previous
                  </Button>
                  <div className="hidden items-center gap-1 sm:flex">
                    {pageWindow(safePage, totalPages).map((n) =>
                      n === "…" ? (
                        <span key={`e-${n}-${safePage}`} className="px-1 text-xs text-ink-faint">
                          …
                        </span>
                      ) : (
                        <button
                          key={n}
                          type="button"
                          onClick={() => setPage(n)}
                          className={`min-w-8 rounded-md px-2 py-1 font-mono text-xs transition-colors duration-fast focus-ring ${
                            n === safePage
                              ? "bg-accent text-white"
                              : "border border-line bg-surface-raised text-ink-muted hover:text-ink"
                          }`}
                        >
                          {n}
                        </button>
                      ),
                    )}
                  </div>
                  <Button
                    variant="secondary"
                    disabled={safePage >= totalPages}
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  >
                    Next
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
        </LoadingOverlay>
      ) : null}
    </section>
  );
}

function AuditTable({ rows }: { rows: AuditItem[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface/80 shadow-panel">
      <table className="min-w-full text-left text-sm">
        <thead className="sticky top-0 border-b border-line bg-surface-raised text-[11px] uppercase tracking-[0.06em] text-ink-faint">
          <tr>
            <th className="whitespace-nowrap px-3 py-2.5 font-medium">ID</th>
            <th className="whitespace-nowrap px-3 py-2.5 font-medium">Actor</th>
            <th className="whitespace-nowrap px-3 py-2.5 font-medium">Action</th>
            <th className="whitespace-nowrap px-3 py-2.5 font-medium">Event</th>
            <th className="whitespace-nowrap px-3 py-2.5 font-medium">Entity</th>
            <th className="whitespace-nowrap px-3 py-2.5 font-medium">Result</th>
            <th className="whitespace-nowrap px-3 py-2.5 font-medium">Confidence</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-t border-line/80 hover:bg-surface-hover/50">
              <td className="px-3 py-2 font-mono text-xs tabular-nums text-ink-faint">#{row.id}</td>
              <td className="px-3 py-2">
                <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${actorText(row.actor)}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${actorDot(row.actor)}`} />
                  {row.actor}
                </span>
              </td>
              <td className="px-3 py-2 text-xs text-ink">{labelize(row.action)}</td>
              <td className="px-3 py-2 text-xs text-ink-muted">{labelize(row.event_type)}</td>
              <td className="max-w-[180px] truncate px-3 py-2 font-mono text-xs text-ink" title={row.entity_id}>
                {row.entity_id}
              </td>
              <td className="px-3 py-2">
                {row.new_state ? <StatusBadge status={row.new_state} /> : <span className="text-xs text-ink-faint">—</span>}
              </td>
              <td className="px-3 py-2 font-mono text-xs tabular-nums text-ink-muted">
                {row.confidence ? conf(row.confidence) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AuditTimeline({ rows }: { rows: AuditItem[] }) {
  return (
    <div className="rounded-lg border border-line bg-surface/80 shadow-panel">
      <ol className="relative px-4 py-2 sm:px-5">
        {rows.map((row, index) => (
          <li key={row.id} className="relative flex gap-4 py-3">
            {index < rows.length - 1 ? (
              <span className="absolute left-[11px] top-8 h-[calc(100%-8px)] w-px bg-line" aria-hidden />
            ) : null}
            <span className="relative z-10 mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line bg-surface-raised">
              <span className={`h-2 w-2 rounded-full ${actorDot(row.actor)}`} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <p className="font-mono text-xs tabular-nums text-ink-faint">#{row.id}</p>
                <p className={`text-sm font-semibold tracking-wide ${actorText(row.actor)}`}>{row.actor}</p>
                <p className="text-sm text-ink-muted">{labelize(row.action)}</p>
                {row.new_state ? <StatusBadge status={row.new_state} /> : null}
              </div>
              <p className="mt-1 text-sm text-ink">{labelize(row.event_type)}</p>
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-ink-faint">
                <span>entity {row.entity_id}</span>
                {row.confidence ? <span>confidence {conf(row.confidence)}</span> : null}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
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
    <label className="flex flex-col gap-1 text-xs text-ink-muted">
      {label}
      <select
        className="min-w-[140px] rounded-md border border-line bg-canvas px-2.5 py-1.5 text-sm text-ink focus-ring"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt === "ALL" ? "All" : labelize(opt)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ViewToggle({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-3 py-1.5 text-xs font-medium transition-colors duration-fast focus-ring ${
        active ? "bg-surface-raised text-ink" : "text-ink-muted hover:text-ink"
      }`}
    >
      {label}
    </button>
  );
}

function isHuman(actor: string): boolean {
  const a = actor.toLowerCase();
  return a.includes("human") || a.includes("finance") || a.includes("review");
}

function actorDot(actor: string): string {
  if (isHuman(actor)) return "bg-warn";
  const a = actor.toLowerCase();
  if (a.includes("ai")) return "bg-info";
  if (a.includes("recon") || a.includes("system") || a.includes("engine")) return "bg-accent";
  return "bg-ink-faint";
}

function actorText(actor: string): string {
  if (isHuman(actor)) return "text-warn-text";
  const a = actor.toLowerCase();
  if (a.includes("ai")) return "text-info-text";
  return "text-ink";
}

function pageWindow(current: number, total: number): Array<number | "…"> {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = new Set<number>([1, total, current, current - 1, current + 1]);
  if (current <= 3) [2, 3, 4].forEach((n) => pages.add(n));
  if (current >= total - 2) [total - 3, total - 2, total - 1].forEach((n) => pages.add(n));
  const sorted = [...pages].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
  const out: Array<number | "…"> = [];
  for (let i = 0; i < sorted.length; i += 1) {
    if (i > 0 && sorted[i] - sorted[i - 1] > 1) out.push("…");
    out.push(sorted[i]);
  }
  return out;
}
