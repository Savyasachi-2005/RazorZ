# AI Layer and Finance Copilot

Two separate AI features, both strictly read-only:

- **Exception assist** — explains one reconciliation break to a reviewer.
- **Finance Copilot** — answers natural-language questions about reconciliation state.

Neither can change a financial value. This document explains what enforces that rather than merely
asserting it.

## Providers

Selected by `AI_PROVIDER`, defaulting to **`mock`**:

| Value | Provider |
|---|---|
| `mock`, `mock-model`, empty | `MockProvider` — deterministic canned analysis, no network |
| `gemini`, `google`, `google_gemini` | Google Gemini via `AI_BASE_URL` |
| `http`, `http_llm`, `openai`, `openai_compatible` | Any OpenAI-compatible endpoint |

An unrecognized value falls back to Gemini when `AI_API_KEY` is set, otherwise raises
`provider_unavailable`.

The mock provider is the default so the entire system, including all 292 tests, runs with no API key
and no network. It is a real code path, not a stub — the same schema validation and the same
integrity guard apply.

## Exception assist

`POST /exceptions/{id}/ai-assist`, modes `full_analysis` (default), `suggest_note`,
`investigation_steps`.

Output schema `AIAssistResult`: `likely_cause`, `explanation`, `investigation_steps` (at least one),
`suggested_action`, `suggested_review_note`, `ai_confidence` (0–1).

### Why it cannot alter a decision

**1. The output schema has no financial field.** There is no `status`, `amount`, `matched_with`,
`exception_type` or `confidence` in `AIAssistResult`. Nothing the model returns maps onto a decision,
so there is no channel through which it could change one.

**2. A runtime integrity guard.** `assist_exception` snapshots the exception's financial fields
before the provider call and re-reads them afterwards:

```python
after_record = repo.get_exception(exception_id)
if after_record is None or _financial_snapshot(after_record) != before:
    raise AIAssistError("AI path attempted to mutate financial data", code="integrity_violation")
```

The snapshot covers `status`, `amount`, `confidence`, `exception_type`, `reviewer_note` and
`resolved_by`. Belt and braces over the schema argument — if a future refactor gave the AI path write
access, the request fails with `integrity_violation` rather than silently corrupting the books.

**3. Evidence is constrained.** `EvidencePacket` is documented as "Compact, token-efficient context.
Money fields are strings; never invent missing ones." Amounts the system does not hold are passed as
`None`, with the comment "Never invent monetary amounts."

**4. The prompt forbids it.** `SYSTEM_PROMPT` states: "You never calculate or modify financial
amounts", "You never invent missing monetary values, orders, payments, settlements, refunds, or
fees", "You never resolve or reject exceptions", and "`ai_confidence` is your confidence in the
explanation (0-1), NOT reconciliation confidence." Prompt instructions are the weakest of the four
layers and are treated accordingly — they are a hint, not the control.

**5. The response is labelled.** Every payload carries `advisory_only: true` and the disclaimer "AI
assistance is advisory. Deterministic reconciliation remains financial truth. AI confidence is not
reconciliation confidence and cannot resolve or reject."

### Failure behaviour

There is **no silent fallback to the mock provider**. A configured provider that fails produces an
error: `provider_unavailable` or `timeout` → `503`; malformed output → `invalid_response` → `422`.
Every attempt, successful or not, is audited via `record_ai_assistance` with no prompt text or
credentials.

Failing loudly is deliberate. Silently substituting canned analysis for a real model would leave a
reviewer unable to tell which they were reading.

## Finance Copilot

`POST /copilot/ask`. Ten read-only tools, and the model never chooses them.

| Tool | Purpose |
|---|---|
| `get_reconciliation_summary` | Latest-run metrics plus the open-exception type mix |
| `search_exceptions` | Filtered exception search with per-type breakdown |
| `get_exception` | Compact detail for one exception |
| `get_audit_events` | Recent audit history, optionally scoped |
| `search_records` | Controlled lookup of stored records |
| `get_record_relationships` | Order → Payment → Settlement/Refund/Fee links |
| `get_financial_summary` | Decimal totals per record type plus unresolved exposure |
| `get_unsettled_payments` | Payments with no settlement pointing at them |
| `get_settlement_summary` | Settlement coverage, orphans, amount mismatches |
| `get_cross_source_summary` | Reconciliation health across all four pair types |

### Deterministic routing

`route()` (`backend/app/copilot/router.py`) is keyword and regex based, checked in fixed order:
mutation patterns → unsupported topics → explicit `EX-<n>` id → follow-up phrasing → record id →
cross-source → settlement → financial → exceptions → audit → summary fallback.

The LLM never selects a tool or writes a query. It receives already-fetched, already-computed data
and turns it into prose. This costs flexibility — unusual phrasings fall back to the reconciliation
summary — and buys a system that cannot be prompt-injected into reading something it shouldn't.

### Read-only guarantees

- **No mutation tool exists.** An unknown tool name returns an error rather than executing.
- **Mutation requests are refused before any work.** Eleven regexes covering resolve, reject,
  approve, refund, create, update, delete, mark-as-resolved and post-journal short-circuit to
  `intent: "mutation_refused"` with no tool call and no LLM call.
- **Unsupported topics answer honestly.** Bank balance, GST/tax, invoice PDFs, profit, forecasts and
  customer master data return an explicit "not available" rather than a guess.
- **No model-generated SQL.** Every parameter is validated against an allow-list, and `MAX_ROWS = 10`
  caps every result.
- **Arithmetic happens in Python** with `Decimal`; the model only narrates numbers it was given.

### Grounding check

`find_ungrounded_claims` scans the generated answer for money amounts and `EX-<n>` ids that do not
appear in the evidence actually fetched. On any hit, **the model's narrative is discarded** and
replaced with a deterministic fallback built from the evidence, `grounding_warning` is set, and the
turn is audited as `ungrounded_response`.

This is the one place the system assumes the model will misbehave and checks its work. A hallucinated
figure in a finance tool is worse than a blunt answer, so a suspect narrative is thrown away rather
than shown with a caveat.

`sources_used` is filtered twice — to known tool names by a schema validator, then to tools that
actually returned data.

Every turn is audited through `record_copilot_query`, storing intent, tools used, provider and
outcome, but no prompt text or keys.

## Measured effect on accuracy: none

The [evaluation harness](EVALUATION.md) runs the held-out batch twice — deterministic-only and
deterministic-plus-AI — on the identical result set. Every accuracy metric is unchanged: precision,
recall, auto-resolve precision, false positives, false negatives, review-required rate and
exception-type accuracy all show a delta of exactly `0.0000`. The advisory layer cost about 41% more
wall time on the mock provider.

That is the expected and correct outcome, not a disappointment. The AI adds explanation and triage
for the human reviewing a break; it was never given a lever that could move a metric. Reported here
plainly because a system claiming AI accuracy gains it cannot demonstrate would be the worse
outcome.
