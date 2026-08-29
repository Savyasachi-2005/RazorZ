import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type AIAssistance, type ExceptionItem } from "../api";
import { ErrorBanner, PageSkeleton, SeverityBadge, StatusBadge, ConfidenceBadge } from "../components/Status";
import { Button } from "../components/ui/Button";
import { SectionCard } from "../components/ui/KpiCard";
import { PageHeader } from "../components/ui/PageHeader";
import { useToast } from "../components/ui/Toast";
import { conf, labelize, money } from "../lib/format";
import { useAppData } from "../state/AppDataContext";

export function ExceptionDetailPage() {
  const { exceptionId } = useParams();
  const id = Number(exceptionId);
  const toast = useToast();
  const {
    exceptions,
    error,
    busy,
    reviewNotes,
    setReviewNote,
    clearError,
    review,
    getException,
    refresh,
  } = useAppData();

  const cached = Number.isInteger(id) ? exceptions.find((row) => row.id === id) ?? null : null;
  const [item, setItem] = useState<ExceptionItem | null>(cached);
  const [loading, setLoading] = useState(!cached && Number.isInteger(id));
  const note = reviewNotes[id] ?? "";

  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [assistance, setAssistance] = useState<AIAssistance | null>(null);
  const [aiMeta, setAiMeta] = useState<{ provider: string; deterministic: number } | null>(null);

  useEffect(() => {
    if (cached) setItem(cached);
  }, [cached]);

  useEffect(() => {
    let cancelled = false;
    if (!Number.isInteger(id)) {
      setLoading(false);
      return;
    }
    if (!cached) setLoading(true);
    setAssistance(null);
    setAiError("");
    setAiMeta(null);
    void (async () => {
      try {
        const row = await getException(id);
        if (!cancelled) setItem(row);
      } catch {
        // surfaced via context
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, getException]);

  async function onReview(action: "resolve" | "reject") {
    if (note.trim().length < 3) return;
    try {
      const updated = await review(id, action, note.trim());
      setItem(updated);
    } catch {
      // surfaced via context
    }
  }

  async function runAiAssist() {
    setAiBusy(true);
    setAiError("");
    try {
      const result = await api.aiAssist(id, "full_analysis");
      setAssistance(result.assistance);
      setAiMeta({
        provider: result.provider,
        deterministic: result.deterministic_confidence,
      });
      toast.success("AI assistance ready", "Advisory only — does not change financial truth.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "AI assistance unavailable";
      setAiError(message);
      toast.warning("AI unavailable", "Continue with rule-based intelligence and manual review.");
    } finally {
      setAiBusy(false);
    }
  }

  function useSuggestion() {
    if (!assistance?.suggested_review_note) return;
    setReviewNote(id, assistance.suggested_review_note);
    toast.info("Suggestion applied", "Edit the note freely before resolve/reject.");
  }

  return (
    <section>
      <Link to="/exceptions" className="text-sm text-ink-muted hover:text-ink">
        ← Exception queue
      </Link>

      {error ? (
        <div className="mt-4">
          <ErrorBanner
            message={error}
            onRetry={() => {
              clearError();
              void refresh();
            }}
          />
        </div>
      ) : null}

      {loading && !item ? (
        <div className="mt-6">
          <PageSkeleton rows={4} />
        </div>
      ) : null}

      {item ? (
        <div className="mt-4 space-y-5">
          <PageHeader
            title={labelize(item.exception_type)}
            subtitle={`Exception EX-${item.id} · ${item.description}`}
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={item.status} showMark />
                <SeverityBadge severity={item.severity} />
                <ConfidenceBadge value={item.confidence} />
              </div>
            }
          />

          <div className="grid gap-3 sm:grid-cols-3">
            <Meta label="Priority" value={item.priority} />
            <Meta label="Certainty" value={item.certainty} />
            <Meta label="Difference" value={money(item.amount)} mono />
          </div>

          <SectionCard title="Transaction evidence" subtitle="Linked identifiers from engine evidence">
            {parseEvidence(item.evidence).length > 0 ? (
              <div className="grid gap-3 md:grid-cols-3">
                {parseEvidence(item.evidence).map((pair) => (
                  <div key={pair.label} className="rounded-md border border-line bg-surface-raised/60 px-3 py-3">
                    <p className="text-[11px] uppercase tracking-wide text-ink-faint">{pair.label}</p>
                    <p className="mt-1 break-all font-mono text-sm text-ink">{pair.value}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="font-mono text-xs text-ink-muted">{item.evidence ?? "No structured evidence payload."}</p>
            )}
          </SectionCard>

          <SectionCard
            title="Exception analysis"
            subtitle="Rule-based / deterministic intelligence — financial truth stays here"
          >
            <dl className="grid gap-4 sm:grid-cols-2">
              <div>
                <dt className="text-[11px] uppercase tracking-wide text-ink-faint">Taxonomy root causes</dt>
                <dd className="mt-1 text-sm text-ink">{item.root_cause ?? "Not confirmed"}</dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-wide text-ink-faint">Deterministic confidence</dt>
                <dd className="mt-1">
                  <ConfidenceBadge value={item.confidence} />
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-[11px] uppercase tracking-wide text-ink-faint">Evidence notes</dt>
                <dd className="mt-1 text-sm text-ink-muted">{item.description}</dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-[11px] uppercase tracking-wide text-ink-faint">Possible root causes</dt>
                <dd className="mt-2 flex flex-wrap gap-2">
                  {item.possible_root_causes.map((cause) => (
                    <span
                      key={cause}
                      className="rounded border border-info/30 bg-info-soft/40 px-2 py-1 text-xs text-info-text"
                    >
                      {cause}
                    </span>
                  ))}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-[11px] uppercase tracking-wide text-ink-faint">Recommended action</dt>
                <dd className="mt-1 text-sm font-medium text-ink">{item.recommended_action}</dd>
              </div>
            </dl>
          </SectionCard>

          <SectionCard
            title="AI Review Assistant"
            subtitle="Advisory investigation help — never resolves, rejects, or changes amounts"
            action={
              <Button variant="secondary" loading={aiBusy} onClick={() => void runAiAssist()}>
                {assistance ? "Regenerate" : "Suggest review note"}
              </Button>
            }
          >
            <p className="mb-3 rounded-md border border-info/30 bg-info-soft/30 px-3 py-2 text-xs text-info-text">
              AI assistance is advisory. Deterministic reconciliation remains financial truth. AI confidence is not
              reconciliation confidence.
            </p>

            {aiError ? (
              <div className="mb-3">
                <ErrorBanner message={aiError} onRetry={() => void runAiAssist()} />
              </div>
            ) : null}

            {!assistance && !aiBusy ? (
              <p className="text-sm text-ink-muted">
                Generate structured guidance for this exception. The result will not submit resolve/reject.
              </p>
            ) : null}

            {aiBusy ? <p className="text-sm text-ink-muted">Analyzing compact evidence packet…</p> : null}

            {assistance ? (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-md border border-line bg-surface-raised/50 px-3 py-3">
                    <p className="text-[11px] uppercase tracking-wide text-ink-faint">Likely cause</p>
                    <p className="mt-1 text-sm text-ink">{assistance.likely_cause}</p>
                  </div>
                  <div className="rounded-md border border-line bg-surface-raised/50 px-3 py-3">
                    <p className="text-[11px] uppercase tracking-wide text-ink-faint">Confidence split</p>
                    <p className="mt-1 text-sm text-ink">
                      Deterministic {aiMeta ? conf(aiMeta.deterministic) : conf(item.confidence)}
                      <span className="text-ink-muted"> · </span>
                      AI {conf(assistance.ai_confidence)}
                    </p>
                    <p className="mt-1 text-[11px] text-ink-faint">Separate metrics — AI never auto-resolves.</p>
                  </div>
                </div>

                <div>
                  <p className="text-[11px] uppercase tracking-wide text-ink-faint">Why review is required</p>
                  <p className="mt-1 text-sm leading-relaxed text-ink-muted">{assistance.explanation}</p>
                </div>

                <div>
                  <p className="text-[11px] uppercase tracking-wide text-ink-faint">AI investigation checklist</p>
                  <ul className="mt-2 space-y-1.5">
                    {assistance.investigation_steps.map((step) => (
                      <li
                        key={step}
                        className="flex gap-2 rounded-md border border-line bg-canvas/40 px-3 py-2 text-sm text-ink"
                      >
                        <span className="mt-0.5 font-mono text-ink-faint" aria-hidden>
                          □
                        </span>
                        <span>{step}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1.5 text-[11px] text-ink-faint">Guidance only — not tracked verification state.</p>
                </div>

                <div>
                  <p className="text-[11px] uppercase tracking-wide text-ink-faint">Suggested next action</p>
                  <p className="mt-1 text-sm font-medium text-ink">{assistance.suggested_action}</p>
                </div>

                <div className="rounded-md border border-accent/30 bg-accent-soft/20 px-3 py-3">
                  <p className="text-[11px] uppercase tracking-wide text-accent-text">AI suggested note</p>
                  <p className="mt-2 text-sm leading-relaxed text-ink">{assistance.suggested_review_note}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button onClick={useSuggestion} disabled={item.status !== "OPEN"}>
                      Use suggestion
                    </Button>
                    <Button variant="secondary" loading={aiBusy} onClick={() => void runAiAssist()}>
                      Regenerate
                    </Button>
                  </div>
                </div>
              </div>
            ) : null}
          </SectionCard>

          {item.resolved_by ? (
            <SectionCard title="Prior review" subtitle="Append-only reviewer record">
              <p className="text-sm text-ink">
                <span className="font-medium">{item.resolved_by}</span>
                <span className="text-ink-muted"> · {item.reviewer_note}</span>
              </p>
            </SectionCard>
          ) : null}

          {item.status === "OPEN" ? (
            <SectionCard title="Review actions" subtitle="Human decision only — AI cannot submit these actions">
              <form
                className="space-y-3"
                onSubmit={(event) => {
                  event.preventDefault();
                }}
              >
                <label className="block text-sm text-ink-muted">
                  Reviewer note
                  <textarea
                    className="mt-1.5 w-full rounded-md border border-line bg-canvas px-3 py-2 text-sm text-ink focus-ring"
                    rows={4}
                    value={note}
                    onChange={(event) => setReviewNote(id, event.target.value)}
                    required
                    minLength={3}
                    placeholder="Document why this break is resolved or rejected…"
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" loading={aiBusy} onClick={() => void runAiAssist()}>
                    Suggest review note
                  </Button>
                </div>
                {note.trim().length > 0 && note.trim().length < 3 ? (
                  <p className="text-xs text-warn-text">Add at least 3 characters before submitting.</p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={busy || note.trim().length < 3}
                    loading={busy}
                    onClick={() => void onReview("resolve")}
                  >
                    Approve / Resolve
                  </Button>
                  <Button
                    variant="danger"
                    disabled={busy || note.trim().length < 3}
                    loading={busy}
                    onClick={() => void onReview("reject")}
                  >
                    Reject
                  </Button>
                  <Button variant="secondary" disabled={busy} onClick={() => setReviewNote(id, "")}>
                    Clear note
                  </Button>
                </div>
                <p className="text-xs text-ink-faint">
                  Use suggestion only fills this textarea. Resolve/Reject remain explicit human actions.
                </p>
              </form>
            </SectionCard>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-surface/80 px-3.5 py-3 shadow-panel">
      <p className="text-[11px] uppercase tracking-wide text-ink-faint">{label}</p>
      <p className={`mt-1 text-sm text-ink ${mono ? "font-mono tabular-nums" : "font-medium"}`}>{value}</p>
    </div>
  );
}

function parseEvidence(raw: string | null): { label: string; value: string }[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed)) {
      return parsed.slice(0, 6).map((row, index) => {
        if (row && typeof row === "object") {
          const obj = row as Record<string, unknown>;
          return {
            label: String(obj.record_id ?? `Candidate ${index + 1}`),
            value: `score ${obj.score ?? "—"}`,
          };
        }
        return { label: `Item ${index + 1}`, value: String(row) };
      });
    }
    if (parsed && typeof parsed === "object") {
      return Object.entries(parsed as Record<string, unknown>).map(([label, value]) => ({
        label: labelize(label),
        value: String(value),
      }));
    }
  } catch {
    // fall through
  }
  return [{ label: "Evidence", value: raw }];
}
