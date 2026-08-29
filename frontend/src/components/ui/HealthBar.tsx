import type { Summary } from "../../api";
import { pct } from "../../lib/format";

export function HealthBar({ summary }: { summary: Summary }) {
  const total = Math.max(summary.total_records, 1);
  const segments = [
    { key: "matched", label: "Matched", value: summary.matched, className: "bg-accent" },
    { key: "exceptions", label: "Exceptions", value: summary.exceptions, className: "bg-danger" },
    { key: "review", label: "Human review", value: summary.review_required, className: "bg-warn" },
    { key: "unresolved", label: "Unresolved", value: summary.unresolved, className: "bg-info" },
  ];

  return (
    <div>
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.08em] text-ink-faint">Match rate</p>
          <p className="mt-1 font-mono text-3xl font-semibold tabular-nums text-accent-text">
            {pct(summary.match_rate)}
          </p>
        </div>
        <p className="text-xs text-ink-muted">{summary.matched} of {summary.total_records} matched</p>
      </div>
      <div className="flex h-3 overflow-hidden rounded-full bg-surface-hover ring-1 ring-line">
        {segments.map((seg) => {
          const width = (seg.value / total) * 100;
          if (width <= 0) return null;
          return (
            <div
              key={seg.key}
              className={`${seg.className} transition-all duration-fast`}
              style={{ width: `${width}%` }}
              title={`${seg.label}: ${seg.value}`}
            />
          );
        })}
      </div>
      <ul className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {segments.map((seg) => (
          <li key={seg.key} className="rounded-md border border-line bg-surface-raised/60 px-3 py-2">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${seg.className}`} />
              <span className="text-[11px] uppercase tracking-wide text-ink-faint">{seg.label}</span>
            </div>
            <p className="mt-1 font-mono text-lg tabular-nums text-ink">{seg.value}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
