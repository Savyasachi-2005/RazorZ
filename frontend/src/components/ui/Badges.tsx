import { labelize } from "../../lib/format";

type Tone = "good" | "warn" | "bad" | "info" | "neutral";

const tones: Record<Tone, string> = {
  good: "border-emerald-800/80 bg-emerald-950/70 text-accent-text",
  warn: "border-amber-800/80 bg-amber-950/60 text-warn-text",
  bad: "border-rose-800/80 bg-rose-950/60 text-danger-text",
  info: "border-blue-800/70 bg-blue-950/50 text-info-text",
  neutral: "border-line bg-surface-raised text-ink-muted",
};

function toneFor(status: string): Tone {
  const s = status.toUpperCase();
  if (["MATCHED", "AUTO_RESOLVED", "RESOLVED", "CONNECTED", "OK", "READY"].includes(s)) return "good";
  if (["REVIEW_REQUIRED", "OPEN", "PARTIAL", "HUMAN REVIEW", "AVAILABLE", "P2", "MEDIUM"].includes(s))
    return "warn";
  if (
    ["EXCEPTION", "UNRESOLVED", "REJECTED", "FAILED", "HIGH", "P1", "DISABLED", "ERROR", "NOT CONFIGURED"].includes(
      s,
    )
  )
    return "bad";
  if (["PROCESSING", "INFO", "PLANNED", "LOW", "P3", "PROBABLE"].includes(s)) return "info";
  return "neutral";
}

const prefix: Record<string, string> = {
  MATCHED: "✓",
  AUTO_RESOLVED: "✓",
  RESOLVED: "✓",
  REVIEW_REQUIRED: "⚠",
  OPEN: "⚠",
  EXCEPTION: "!",
  UNRESOLVED: "!",
  REJECTED: "!",
};

export function StatusBadge({ status, showMark = false }: { status: string; showMark?: boolean }) {
  const mark = showMark ? prefix[status.toUpperCase()] : undefined;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-wide ${tones[toneFor(status)]}`}
    >
      {mark ? <span aria-hidden>{mark}</span> : null}
      {labelize(status)}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const s = severity.toLowerCase();
  const tone: Tone = s === "high" ? "bad" : s === "medium" ? "warn" : s === "low" ? "info" : "neutral";
  return (
    <span className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${tones[tone]}`}>
      {severity}
    </span>
  );
}

export function ConfidenceBadge({ value }: { value: string }) {
  const n = Number(value);
  const pctVal = Number.isFinite(n) ? (n <= 1 ? n * 100 : n) : NaN;
  const tone: Tone = !Number.isFinite(pctVal)
    ? "neutral"
    : pctVal >= 99
      ? "good"
      : pctVal >= 70
        ? "warn"
        : "bad";
  const label = Number.isFinite(pctVal) ? `${pctVal.toFixed(0)}%` : value;
  return (
    <span className={`inline-flex rounded border px-2 py-0.5 font-mono text-[11px] tabular-nums ${tones[tone]}`}>
      {label}
    </span>
  );
}
