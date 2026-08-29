import { useCallback, useEffect, useRef, useState } from "react";
import { api, type CopilotResponse, type CopilotTurn } from "../api";
import { EmptyState, ErrorBanner } from "../components/Status";
import { Button } from "../components/ui/Button";
import { IconClose, IconCopilot } from "../components/ui/Icons";
import { PageHeader } from "../components/ui/PageHeader";
import { labelize } from "../lib/format";

const FALLBACK_SUGGESTIONS = [
  "Summarize today's reconciliation",
  "Show unresolved exceptions",
  "What is causing the most exceptions?",
  "How much money is unresolved?",
];

type Entry = {
  id: number;
  question: string;
  response: CopilotResponse;
};

const THREAD_STORAGE_KEY = "razorz.copilot.thread";

/** Keep the thread across page navigation and reloads until the user clears it. */
function loadThread(): Entry[] {
  try {
    const raw = sessionStorage.getItem(THREAD_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Entry[]) : [];
  } catch {
    return [];
  }
}

function saveThread(entries: Entry[]): void {
  try {
    if (entries.length) {
      sessionStorage.setItem(THREAD_STORAGE_KEY, JSON.stringify(entries));
    } else {
      sessionStorage.removeItem(THREAD_STORAGE_KEY);
    }
  } catch {
    // Storage full or unavailable — the in-memory thread still works.
  }
}

// Findings arrive as "Current status: REJECTED — ...". Surface the label so the
// status / cause / human decision structure is readable at a glance.
function splitFinding(finding: string): [string | null, string] {
  const separator = finding.indexOf(": ");
  if (separator < 1 || separator > 42) return [null, finding];
  const label = finding.slice(0, separator);
  if (/[.!?]/.test(label)) return [null, finding];
  return [label, finding.slice(separator + 2)];
}

export function CopilotPage() {
  const [question, setQuestion] = useState("");
  const [entries, setEntries] = useState<Entry[]>(loadThread);
  const [suggestions, setSuggestions] = useState<string[]>(FALLBACK_SUGGESTIONS);
  const [providerUnavailable, setProviderUnavailable] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const answerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    saveThread(entries);
  }, [entries]);

  useEffect(() => {
    void api
      .copilotSuggestions()
      .then((data) => {
        if (data.suggestions?.length) setSuggestions(data.suggestions);
      })
      .catch(() => {
        // Suggestions are cosmetic — keep the static list if the call fails.
      });
  }, []);

  const ask = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || loading) return;
      setLoading(true);
      setError("");
      setProviderUnavailable(false);

      // Short conversational context so follow-ups like "which one is largest?" resolve.
      const history: CopilotTurn[] = entries.slice(-2).flatMap((entry) => [
        { role: "user" as const, content: entry.question },
        { role: "assistant" as const, content: entry.response.answer.answer },
      ]);

      try {
        const response = await api.copilotAsk(text, history);
        setEntries((prev) => [...prev, { id: Date.now(), question: text, response }]);
        setQuestion("");
        requestAnimationFrame(() => {
          answerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : "Copilot request failed";
        setError(message);
        if (/provider|api key|unavailable|timed out/i.test(message)) setProviderUnavailable(true);
      } finally {
        setLoading(false);
      }
    },
    [entries, loading],
  );

  const removeEntry = useCallback((id: number) => {
    setEntries((prev) => prev.filter((entry) => entry.id !== id));
  }, []);

  const latest = entries[entries.length - 1];

  return (
    <section>
      <PageHeader
        title="Finance Copilot"
        subtitle="Ask about your financial operations. Answers are grounded in RAZORZ reconciliation data — read-only."
        actions={
          entries.length ? (
            <>
              <span className="text-xs text-ink-faint">
                {entries.length} saved {entries.length === 1 ? "answer" : "answers"}
              </span>
              <Button variant="secondary" onClick={() => setEntries([])} disabled={loading}>
                Clear all
              </Button>
            </>
          ) : null
        }
      />

      <form
        className="mb-4"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(question);
        }}
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Why did today's reconciliation rate fall?"
            maxLength={1000}
            className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus-ring"
            aria-label="Ask the Finance Copilot"
          />
          <Button type="submit" loading={loading} disabled={!question.trim()} className="shrink-0">
            {loading ? "Thinking…" : "Ask Copilot"}
          </Button>
        </div>
      </form>

      <div className="mb-5 flex flex-wrap gap-2">
        {suggestions.map((item) => (
          <button
            key={item}
            type="button"
            disabled={loading}
            onClick={() => void ask(item)}
            className="rounded-full border border-line bg-surface/70 px-3 py-1.5 text-xs text-ink-muted transition-colors duration-fast hover:border-line-strong hover:text-ink focus-ring disabled:opacity-50"
          >
            {item}
          </button>
        ))}
      </div>

      {error ? (
        <div className="mb-4">
          <ErrorBanner
            message={
              providerUnavailable
                ? `${error} — set AI_PROVIDER=mock to use the offline analyst, or configure AI_API_KEY.`
                : error
            }
            onRetry={() => {
              setError("");
              if (latest) void ask(latest.question);
            }}
          />
        </div>
      ) : null}

      {loading ? (
        <div className="mb-4 rounded-lg border border-line bg-surface/70 px-4 py-5 shadow-panel">
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
            Retrieving RAZORZ data and preparing a grounded answer…
          </div>
        </div>
      ) : null}

      {!entries.length && !loading && !error ? (
        <EmptyState
          icon={<IconCopilot width={28} height={28} />}
          title="Ask your first question"
          body="The Copilot reads reconciliation results, exceptions, records, and the audit trail. It explains the numbers the engine already computed — it never changes them."
        />
      ) : null}

      {/* Newest answer on top; `entries` stays chronological for conversation history. */}
      <div className="space-y-4">
        {entries
          .slice()
          .reverse()
          .map((entry, index) => (
            <div key={entry.id} ref={index === 0 ? answerRef : undefined}>
              <AnswerCard entry={entry} onDelete={() => removeEntry(entry.id)} />
            </div>
          ))}
      </div>

      {entries.length ? (
        <p className="mt-6 border-t border-line pt-4 text-xs leading-relaxed text-ink-faint">
          The Copilot is read-only and provides analytical assistance based on RAZORZ data.
          Deterministic reconciliation remains financial truth; resolve and reject stay with human
          reviewers in Exceptions.
        </p>
      ) : null}
    </section>
  );
}

function AnswerCard({ entry, onDelete }: { entry: Entry; onDelete: () => void }) {
  const { question, response } = entry;
  const { answer } = response;

  return (
    <article className="rounded-lg border border-line bg-surface/80 shadow-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <p className="min-w-0 text-sm font-medium text-ink">{question}</p>
        <div className="flex shrink-0 items-center gap-2">
          {response.refused ? (
            <span className="rounded-full border border-warn/40 bg-amber-950/30 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-warn">
              read-only
            </span>
          ) : null}
          <span className="rounded-full border border-line bg-surface-raised px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-ink-faint">
            {response.llm_used ? response.provider : "deterministic"}
          </span>
          <button
            type="button"
            onClick={onDelete}
            aria-label={`Delete answer for: ${question}`}
            title="Delete this answer"
            className="rounded p-1 text-ink-faint transition-colors duration-fast hover:bg-surface-hover hover:text-danger-text focus-ring"
          >
            <IconClose width={14} height={14} />
          </button>
        </div>
      </header>

      <div className="px-4 py-4">
        {response.grounding_warning ? (
          <p className="mb-3 rounded-md border border-warn/40 bg-amber-950/20 px-3 py-2 text-xs text-warn">
            {response.grounding_warning}
          </p>
        ) : null}

        <p className="text-sm leading-relaxed text-ink">{answer.answer}</p>

        {answer.key_findings.length ? (
          <div className="mt-4">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
              Key findings
            </p>
            <ul className="mt-2 space-y-1.5">
              {answer.key_findings.map((finding) => {
                const [label, detail] = splitFinding(finding);
                return (
                  <li key={finding} className="flex gap-2 text-sm text-ink-muted">
                    <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-accent" />
                    <span className="min-w-0">
                      {label ? (
                        <span className="font-semibold text-ink">{label}: </span>
                      ) : null}
                      {detail}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}

        {answer.data_points.length ? (
          <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {answer.data_points.map((point) => (
              <div
                key={`${point.label}-${point.value}`}
                className="rounded-md border border-line bg-surface-raised/60 px-3 py-2"
              >
                <p className="text-[10px] uppercase tracking-[0.1em] text-ink-faint">{point.label}</p>
                <p className="mt-0.5 font-mono text-sm tabular-nums text-ink">{point.value}</p>
              </div>
            ))}
          </div>
        ) : null}

        {response.tools_used.length ? (
          <div className="mt-4 border-t border-line pt-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
              Data sources
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {response.tools_used.map((tool) => (
                <span
                  key={tool}
                  className="rounded border border-line bg-surface px-2 py-0.5 font-mono text-[10px] text-ink-muted"
                >
                  {labelize(tool)}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {response.tool_errors.length ? (
          <p className="mt-3 text-xs text-warn">
            Some data could not be read: {response.tool_errors.map((item) => item.tool).join(", ")}
          </p>
        ) : null}
      </div>
    </article>
  );
}
