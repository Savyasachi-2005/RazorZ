import type { ReactNode } from "react";

type Props = {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad" | "info";
  dense?: boolean;
};

const toneValue: Record<NonNullable<Props["tone"]>, string> = {
  default: "text-ink",
  good: "text-accent-text",
  warn: "text-warn-text",
  bad: "text-danger-text",
  info: "text-info-text",
};

export function KpiCard({ label, value, hint, tone = "default", dense }: Props) {
  return (
    <div
      className={`rounded-lg border border-line bg-surface/80 shadow-panel ${
        dense ? "px-3.5 py-3" : "px-4 py-4"
      }`}
    >
      <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-ink-faint">{label}</p>
      <p className={`mt-2 font-mono text-2xl font-semibold tabular-nums leading-none sm:text-[1.75rem] ${toneValue[tone]}`}>
        {value}
      </p>
      {hint ? <p className="mt-2 text-xs text-ink-muted">{hint}</p> : null}
    </div>
  );
}

export function SectionCard({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-line bg-surface/80 shadow-panel ${className}`}>
      <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3.5">
        <div>
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p> : null}
        </div>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}
