export type Summary = {
  total_records: number;
  matched: number;
  exceptions: number;
  review_required: number;
  unresolved: number;
  match_rate: number;
};

export type ReconciliationRecord = {
  id: number;
  run_id: string;
  record_id: string;
  matched_with: string | null;
  pair_type?: string | null;
  source_record_type?: string | null;
  related_record_type?: string | null;
  order_id?: string | null;
  payment_id?: string | null;
  settlement_id?: string | null;
  refund_id?: string | null;
  fee_id?: string | null;
  status: string;
  confidence: string;
  amount_diff: string;
  exception_type: string | null;
};

export type ExceptionItem = {
  id: number;
  exception_type: string;
  status: string;
  severity: string;
  certainty: string;
  confidence: string;
  amount: string | null;
  description: string;
  evidence: string | null;
  root_cause: string | null;
  recommended_action: string | null;
  human_required: boolean;
  priority: string;
  reviewer_note: string | null;
  resolved_by: string | null;
  possible_root_causes: string[];
};

export type AuditItem = {
  id: number;
  event_type: string;
  entity_id: string;
  actor: string;
  action: string;
  new_state: string | null;
  confidence: string | null;
};

export type AIAssistMode = "full_analysis" | "suggest_note" | "investigation_steps";

export type AIAssistance = {
  likely_cause: string;
  explanation: string;
  investigation_steps: string[];
  suggested_action: string;
  suggested_review_note: string;
  ai_confidence: number;
};

export type AIAssistResponse = {
  exception_id: number;
  mode: AIAssistMode;
  provider: string;
  deterministic_confidence: number;
  assistance: AIAssistance;
  advisory_only: boolean;
  disclaimer: string;
  evidence_packet?: Record<string, unknown>;
};

export type CopilotTurn = {
  role: "user" | "assistant";
  content: string;
};

export type CopilotDataPoint = {
  label: string;
  value: string;
};

export type CopilotAnswer = {
  answer: string;
  key_findings: string[];
  data_points: CopilotDataPoint[];
  sources_used: string[];
  confidence: number;
};

export type CopilotResponse = {
  question: string;
  intent: string;
  provider: string;
  llm_used: boolean;
  read_only: boolean;
  refused: boolean;
  answer: CopilotAnswer;
  tools_used: string[];
  tool_errors: { tool: string; error: string | null }[];
  evidence: Record<string, unknown>;
  disclaimer: string;
  grounding_warning?: string;
};

export type CopilotSuggestions = {
  suggestions: string[];
  tools: string[];
  read_only: boolean;
  disclaimer: string;
};

export type Paginated<T> = {
  items: T[];
  limit: number;
  offset: number;
};

export type RazorpayStatus = {
  provider: string;
  mode: string;
  configured: boolean;
  connected: boolean;
  key_id_prefix?: string | null;
  capabilities?: Record<string, string>;
  notes?: string[];
  error?: { code: string; message: string };
};

export type RazorpaySyncResult = {
  source: string;
  mode: string;
  synced: boolean;
  empty: boolean;
  counts: Record<string, number>;
  summary: { total: number; matched: number; exceptions: number; review_required?: number; match_rate: number };
  message: string;
  persisted?: boolean;
  run_id?: string;
};

// Sent when the backend has API auth enabled. A browser bundle cannot hold a
// real secret, so this must be a UI-scoped key, not an admin credential.
const API_KEY = (import.meta.env.VITE_API_KEY ?? "").trim();

const TOKEN_STORAGE_KEY = "razorz.session.token";

export type AuthUser = {
  id: number;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  last_login_at: string | null;
};

export type LoginResponse = {
  token: string;
  token_type: string;
  expires_at: string;
  user: AuthUser;
};

/** Session token lives in sessionStorage: cleared when the tab closes. */
export const session = {
  get: () => {
    try {
      return sessionStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
    } catch {
      return "";
    }
  },
  set: (token: string) => {
    try {
      sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
    } catch {
      /* storage unavailable — the session stays in memory only */
    }
  },
  clear: () => {
    try {
      sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch {
      /* nothing to clear */
    }
  },
};

/** Raised on 401 so the UI can return to the login screen. */
export class UnauthorizedError extends Error {
  constructor(message = "Your session has expired. Please sign in again.") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = session.get();
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(API_KEY && !token ? { "X-API-Key": API_KEY } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (response.status === 401 && !path.startsWith("/auth/login")) {
    session.clear();
    onUnauthorized?.();
    throw new UnauthorizedError();
  }
  const raw = await response.text();
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok) {
    if (raw.trimStart().toLowerCase().startsWith("<!doctype") || raw.trimStart().startsWith("<html")) {
      throw new Error(
        "API returned HTML instead of JSON — is the backend running on http://127.0.0.1:8000?",
      );
    }
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      if (parsed?.detail && typeof parsed.detail === "object" && parsed.detail !== null) {
        const obj = parsed.detail as { message?: string; code?: string };
        throw new Error(obj.message || raw || `Request failed (${response.status})`);
      }
      if (typeof parsed?.detail === "string") {
        throw new Error(parsed.detail);
      }
    } catch (err) {
      if (err instanceof Error && err.message !== raw) throw err;
    }
    throw new Error(raw || `Request failed (${response.status})`);
  }
  if (!contentType.includes("application/json") && raw.trimStart().startsWith("<")) {
    throw new Error(
      "API returned HTML instead of JSON — is the backend running on http://127.0.0.1:8000?",
    );
  }
  if (!raw.trim()) {
    throw new Error(
      `API returned an empty response for ${path} — the request never reached the backend. ` +
        "Check that uvicorn is running on port 8000 and that this route is proxied in vite.config.ts.",
    );
  }
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error("API returned invalid JSON — check that uvicorn is running on port 8000.");
  }
}

export const api = {
  login: (email: string, password: string) =>
    request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<{ logged_out: boolean }>("/auth/logout", { method: "POST" }),
  me: () =>
    request<{ authenticated: boolean; user: AuthUser | null; auth_method: string }>("/auth/me"),
  health: () =>
    request<{ status: string; service?: string; database?: { ok: boolean; dialect?: string } }>("/health"),
  summary: () => request<Summary>("/reconciliation/summary"),
  records: (pairType?: string) => {
    const params = new URLSearchParams({ limit: "200" });
    if (pairType && pairType !== "ALL") params.set("pair_type", pairType);
    return request<Paginated<ReconciliationRecord>>(`/reconciliation/records?${params}`);
  },
  exceptions: () => request<Paginated<ExceptionItem>>("/exceptions?limit=50"),
  exception: (id: number) => request<ExceptionItem>(`/exceptions/${id}`),
  audit: () => request<Paginated<AuditItem>>("/audit?limit=100"),
  generate: (records = 50) =>
    request<{ summary: { match_rate: number; total: number; matched: number; exceptions: number } }>(
      "/ingestion/generate",
      { method: "POST", body: JSON.stringify({ records, seed: 42 }) },
    ),
  review: (id: number, action: "resolve" | "reject", note: string) =>
    request<ExceptionItem>(`/exceptions/${id}/${action}`, {
      method: "POST",
      body: JSON.stringify({ actor: "finance-ops", note }),
    }),
  aiAssist: (id: number, mode: AIAssistMode = "full_analysis") =>
    request<AIAssistResponse>(`/exceptions/${id}/ai-assist`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  copilotSuggestions: () => request<CopilotSuggestions>("/copilot/suggestions"),
  copilotAsk: (question: string, history: CopilotTurn[] = []) =>
    request<CopilotResponse>("/copilot/ask", {
      method: "POST",
      body: JSON.stringify({ question, history: history.slice(-4) }),
    }),
  razorpayStatus: () => request<RazorpayStatus>("/integrations/razorpay/status"),
  razorpaySync: (count = 50) =>
    request<RazorpaySyncResult>("/integrations/razorpay/sync", {
      method: "POST",
      body: JSON.stringify({ count }),
    }),
};
