import type { ReactNode } from "react";
import { Button } from "./Button";

export function EmptyState({
  title,
  body,
  action,
  icon,
}: {
  title: string;
  body: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed border-line-strong bg-surface/40 px-6 py-14 text-center">
      {icon ? <div className="mb-4 text-ink-faint">{icon}</div> : null}
      <p className="text-base font-semibold text-ink">{title}</p>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">{body}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      className="flex flex-col gap-3 rounded-md border border-danger/40 bg-danger-soft/60 px-4 py-3 text-sm text-danger-text sm:flex-row sm:items-center sm:justify-between"
      role="alert"
    >
      <div>
        <p className="font-medium">Something went wrong</p>
        <p className="mt-0.5 text-danger-text/90">{message}</p>
      </div>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry} className="shrink-0">
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-surface-hover ${className}`} />;
}

export function PageSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-[88px]" />
        ))}
      </div>
      <Skeleton className="h-40" />
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={`r-${i}`} className="h-10" />
      ))}
    </div>
  );
}
