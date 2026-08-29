import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  type AuditItem,
  type ExceptionItem,
  type ReconciliationRecord,
  type Summary,
} from "../api";
import { useToast } from "../components/ui/Toast";
import { pct } from "../lib/format";

type RefreshKey = "summary" | "records" | "exceptions" | "audit" | "health";

type AppData = {
  summary: Summary | null;
  records: ReconciliationRecord[];
  exceptions: ExceptionItem[];
  audit: AuditItem[];
  healthStatus: "ok" | "degraded" | "unknown";
  notice: string;
  error: string;
  initialLoading: boolean;
  refreshing: boolean;
  busy: boolean;
  busyLabel: string;
  reviewNotes: Record<number, string>;
  setReviewNote: (id: number, note: string) => void;
  clearError: () => void;
  clearNotice: () => void;
  refresh: (opts?: { silent?: boolean; keys?: RefreshKey[] }) => Promise<void>;
  generate: (records?: number) => Promise<void>;
  review: (id: number, action: "resolve" | "reject", note: string) => Promise<ExceptionItem>;
  getException: (id: number) => Promise<ExceptionItem>;
};

const AppDataContext = createContext<AppData | null>(null);

/** Survive React StrictMode remounts in dev. */
let workspaceBooted = false;

const ALL_KEYS: RefreshKey[] = ["summary", "records", "exceptions", "audit", "health"];

export function AppDataProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [records, setRecords] = useState<ReconciliationRecord[]>([]);
  const [exceptions, setExceptions] = useState<ExceptionItem[]>([]);
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [healthStatus, setHealthStatus] = useState<"ok" | "degraded" | "unknown">("unknown");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyLabel, setBusyLabel] = useState("");
  const [reviewNotes, setReviewNotes] = useState<Record<number, string>>({});
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const refreshInFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async (opts?: { silent?: boolean; keys?: RefreshKey[] }) => {
    const keys = opts?.keys?.length ? opts.keys : ALL_KEYS;

    // Allow lightweight partial refreshes to run even if a full refresh is in flight,
    // but coalesce identical full refreshes.
    const isFull = keys.length === ALL_KEYS.length;
    if (isFull && refreshInFlight.current) {
      await refreshInFlight.current;
      return;
    }

    if (!opts?.silent) setError("");
    setRefreshing(true);
    const work = (async () => {
      try {
        const tasks: Promise<void>[] = [];

        if (keys.includes("summary")) {
          tasks.push(api.summary().then((s) => setSummary(s)));
        }
        if (keys.includes("records")) {
          tasks.push(api.records().then((r) => setRecords(r.items)));
        }
        if (keys.includes("exceptions")) {
          tasks.push(api.exceptions().then((e) => setExceptions(e.items)));
        }
        if (keys.includes("audit")) {
          tasks.push(api.audit().then((a) => setAudit(a.items)));
        }
        if (keys.includes("health")) {
          tasks.push(
            api
              .health()
              .then((h) => setHealthStatus(h.status === "ok" ? "ok" : "degraded"))
              .catch(() => setHealthStatus("degraded")),
          );
        }

        await Promise.all(tasks);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load workspace data";
        setError(message);
        setHealthStatus("degraded");
        toastRef.current.error("Could not load data", message);
      } finally {
        setRefreshing(false);
        setInitialLoading(false);
        if (isFull) refreshInFlight.current = null;
      }
    })();

    if (isFull) refreshInFlight.current = work;
    await work;
  }, []);

  useEffect(() => {
    if (workspaceBooted) {
      setInitialLoading(false);
      return;
    }
    workspaceBooted = true;
    void (async () => {
      await refresh({ silent: true });
      toastRef.current.info("Workspace ready", "Latest ledger data is loaded.");
    })();
  }, [refresh]);

  const generate = useCallback(
    async (count = 50) => {
      setBusy(true);
      setBusyLabel(`Running ${count}-record batch…`);
      setError("");
      toastRef.current.info("Batch started", `Generating and reconciling ${count} records.`);
      try {
        const result = await api.generate(count);
        const message = `${result.summary.matched}/${result.summary.total} matched · ${pct(result.summary.match_rate)}`;
        setNotice(`Batch complete · ${message}`);
        toastRef.current.success("Batch complete", message);
        setBusy(false);
        setBusyLabel("");
        await refresh({ silent: true });
      } catch (err) {
        const message = err instanceof Error ? err.message : "Generate failed";
        setError(message);
        toastRef.current.error("Batch failed", message);
        throw err;
      } finally {
        setBusy(false);
        setBusyLabel("");
      }
    },
    [refresh],
  );

  const review = useCallback(
    async (id: number, action: "resolve" | "reject", note: string) => {
      setBusy(true);
      setBusyLabel(action === "resolve" ? "Resolving exception…" : "Rejecting exception…");
      setError("");
      try {
        const updated = await api.review(id, action, note);
        setExceptions((prev) => {
          const next = prev.map((row) => (row.id === id ? updated : row));
          if (!next.some((row) => row.id === id)) next.unshift(updated);
          return next;
        });
        // Optimistic audit row so Audit trail updates instantly without a full reload.
        const label = action === "resolve" ? "resolved" : "rejected";
        setAudit((prev) => [
          {
            id: Date.now(),
            event_type: "human_review",
            entity_id: String(id),
            actor: "finance-ops",
            action,
            new_state: action === "resolve" ? "RESOLVED" : "REJECTED",
            confidence: null,
          },
          ...prev,
        ].slice(0, 100));
        setReviewNotes((prev) => {
          const copy = { ...prev };
          delete copy[id];
          return copy;
        });
        setNotice(`Exception EX-${id} ${label}.`);
        toastRef.current.success(`Exception ${label}`, `EX-${id} was ${label} and audited.`);
        // Resolve/reject only changes exception + one audit event — do NOT reload the whole workspace.
        return updated;
      } catch (err) {
        const message = err instanceof Error ? err.message : "Review failed";
        setError(message);
        toastRef.current.error("Review failed", message);
        throw err;
      } finally {
        setBusy(false);
        setBusyLabel("");
      }
    },
    [],
  );

  const exceptionsRef = useRef(exceptions);
  exceptionsRef.current = exceptions;

  const getException = useCallback(async (id: number) => {
    const cached = exceptionsRef.current.find((row) => row.id === id);
    if (cached) return cached;
    const row = await api.exception(id);
    setExceptions((prev) => (prev.some((item) => item.id === id) ? prev : [row, ...prev]));
    return row;
  }, []);

  const value = useMemo<AppData>(
    () => ({
      summary,
      records,
      exceptions,
      audit,
      healthStatus,
      notice,
      error,
      initialLoading,
      refreshing,
      busy,
      busyLabel,
      reviewNotes,
      setReviewNote: (id, note) => setReviewNotes((prev) => ({ ...prev, [id]: note })),
      clearError: () => setError(""),
      clearNotice: () => setNotice(""),
      refresh,
      generate,
      review,
      getException,
    }),
    [
      summary,
      records,
      exceptions,
      audit,
      healthStatus,
      notice,
      error,
      initialLoading,
      refreshing,
      busy,
      busyLabel,
      reviewNotes,
      refresh,
      generate,
      review,
      getException,
    ],
  );

  return <AppDataContext.Provider value={value}>{children}</AppDataContext.Provider>;
}

export function useAppData(): AppData {
  const ctx = useContext(AppDataContext);
  if (!ctx) throw new Error("useAppData must be used within AppDataProvider");
  return ctx;
}
