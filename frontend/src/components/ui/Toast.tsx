import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type ToastTone = "success" | "error" | "info" | "warning";

export type ToastInput = {
  title: string;
  description?: string;
  tone?: ToastTone;
  durationMs?: number;
};

type Toast = ToastInput & {
  id: string;
  tone: ToastTone;
  durationMs: number;
};

type ToastContextValue = {
  push: (toast: ToastInput) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
  warning: (title: string, description?: string) => void;
  dismiss: (id: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const toneStyles: Record<ToastTone, string> = {
  success: "border-accent/50 bg-[#0f1f17] text-accent-text",
  error: "border-danger/50 bg-[#2a1218] text-danger-text",
  info: "border-info/50 bg-[#0f172a] text-info-text",
  warning: "border-warn/50 bg-[#27180a] text-warn-text",
};

const toneDot: Record<ToastTone, string> = {
  success: "bg-accent",
  error: "bg-danger",
  info: "bg-info",
  warning: "bg-warn",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((toast: ToastInput) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const next: Toast = {
      id,
      title: toast.title,
      description: toast.description,
      tone: toast.tone ?? "info",
      durationMs: toast.durationMs ?? 4200,
    };
    setToasts((prev) => [...prev.slice(-3), next]);
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({
      push,
      dismiss,
      success: (title, description) => push({ title, description, tone: "success" }),
      error: (title, description) => push({ title, description, tone: "error", durationMs: 6500 }),
      info: (title, description) => push({ title, description, tone: "info" }),
      warning: (title, description) => push({ title, description, tone: "warning" }),
    }),
    [push, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed bottom-5 right-5 z-[80] flex w-[min(calc(100vw-2.5rem),22rem)] flex-col gap-2"
        aria-live="polite"
        aria-relevant="additions"
      >
        {toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} onDismiss={() => dismiss(toast.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastCard({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => {
    if (toast.durationMs <= 0) return;
    const timer = window.setTimeout(onDismiss, toast.durationMs);
    return () => window.clearTimeout(timer);
  }, [toast.durationMs, onDismiss]);

  return (
    <div
      className={`pointer-events-auto w-full rounded-lg border px-3.5 py-3 shadow-panel ${toneStyles[toast.tone]}`}
      role="status"
    >
      <div className="flex items-start gap-2.5">
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${toneDot[toast.tone]}`} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink">{toast.title}</p>
          {toast.description ? (
            <p className="mt-0.5 break-words text-xs leading-relaxed text-ink-muted">{toast.description}</p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 rounded px-1 text-xs text-ink-faint hover:text-ink focus-ring"
          aria-label="Dismiss notification"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
