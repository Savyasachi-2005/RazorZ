# Evaluation

A held-out harness that measures reconciliation accuracy against known ground truth, plus a
baseline-versus-hybrid comparison that tests whether the AI layer changes any measured outcome.

## Isolation

`backend/app/evaluation/__init__.py` states the invariant: "Nothing in `app.evaluation` is imported
by the reconciliation engine, the Copilot or the API — the evaluation dataset must never influence
inference."

The held-out seed is **`90210`**, deliberately different from the development seed `42`, so no record
the engine was tuned against appears in the evaluation set. Isolation is structural, not procedural:
the serving path cannot import evaluation data even by accident.

## Ground truth comes first

Labels are recorded **when each case is constructed**, not derived from engine output — otherwise the
harness would grade the engine against itself and always score perfectly.

`GroundTruthLabel` is frozen and carries `record_id`, `pair_type`, `scenario`, `expected_outcome`
(`MATCH` or `EXCEPTION`), `counterpart_id`, `expected_exception_type` and `amount`.
`EvaluationDataset.engine_records()` emits only production-shaped dicts and "carries no ground
truth", so the engine sees exactly what it would see in production.

### Nine scenarios

| Scenario | Expected | Injected condition |
|---|---|---|
| `clean_match` | MATCH | order and payment agree exactly |
| `missing_payment` | EXCEPTION `PAYMENT_MISSING` | order with no payment |
| `duplicate_settlement` | EXCEPTION `DUPLICATE_SETTLEMENT` | second settlement for one payment |
| `amount_mismatch` | EXCEPTION `AMOUNT_MISMATCH` | payment differs by `40.00` |
| `fee_discrepancy` | EXCEPTION `FEE_DIFFERENCE` | fee off by `5.00` |
| `refund` | MATCH | refund at 25% of the payment |
| `partial_settlement` | EXCEPTION `SETTLEMENT_AMOUNT_MISMATCH` | settlement short by `30.00` |
| `ambiguous_match` | EXCEPTION *(type not asserted)* | two candidate payments at `+15.00` / `+16.00` |
| `orphan_payment` | EXCEPTION `ORPHAN_PAYMENT` | payment with no order |

For `ambiguous_match` the requirement is only that neither candidate is auto-accepted; the specific
exception type is not asserted, because more than one classification is defensible.

## Metrics, as implemented

A decision counts as **auto-accepted** only when a prediction exists, its status is `MATCHED` or
`AUTO_RESOLVED`, **and** `matched_with` is not `None`. Because `REVIEW_REQUIRED` is excluded, sending
something to a human can never be scored as a false match — review is a safe outcome, not an error.

| Metric | Definition |
|---|---|
| `precision` | TP / (TP + FP) |
| `recall` | TP / (TP + FN) |
| `f1` | 2PR / (P + R) |
| `auto_resolve_precision` | correct auto-accepts / all auto-accepts |
| `exception_type_accuracy` | correct exception types / exceptions where a type was asserted |
| `review_required_rate` | `REVIEW_REQUIRED` count / labels |
| `throughput_records_per_second` | records / elapsed seconds |

Ratios are quantized to four decimal places and return `None` rather than zero when the denominator
is zero — an unmeasurable metric is reported as unmeasurable.

Scoring per label:

1. Expected MATCH, linked to the right counterpart → **true positive**.
2. Expected MATCH, linked to the **wrong** counterpart → **both a false positive and a false
   negative**, since a link was asserted incorrectly *and* the true match was missed.
3. Expected MATCH, not auto-accepted → **false negative**.
4. Expected EXCEPTION, auto-accepted → **false positive**. Otherwise a **true negative**, and if a
   type was asserted, the type is checked.

Counters accumulate simultaneously into `overall`, `by_pair_type` and `by_scenario`. Errors are
classified as `false_match`, `missed_match` or `wrong_exception_type`, and the report prints the first
ten with a count of the remainder. Labels are keyed by `(record_id, pair_type)`, so one record can
hold several labels — one per relationship.

## Measured results

Seed `90210`, provider `mock`.

**62 records / 48 labels** (`--cycles 2`): precision `1.0000`, recall `1.0000`, auto-resolve precision
`1.0000`, exception-type accuracy `1.0000`, review-required rate `0.1667`, 0 false positives, 0 false
negatives.

**1,054 records / 816 labels** (`--cycles 34`): precision `1.0000`, recall `1.0000`, auto-resolve
precision `1.0000` across 510 auto-accepted links, review-required 146 (rate `0.1789`),
exception-type accuracy `0.9580`, roughly 2,000 records/second.

### The known accuracy gap

At scale, **34 `missing_payment` orders are classified `AMBIGUOUS_MATCH` instead of
`PAYMENT_MISSING`** — the entire shortfall in exception-type accuracy. Both statuses correctly route
to human review, so no money is mis-stated and precision and recall are unaffected; only the label a
reviewer sees is wrong.

**This is deliberately unfixed.** Correcting it by observing held-out failures would be tuning on the
test set, which destroys the measurement's value. It stays open until it can be addressed with
development data.

## Baseline versus hybrid

Two modes: `deterministic_only` and `deterministic_plus_ai`.

`compare_modes` calls `reconcile_records(records)` **once** and scores the identical result set
twice. The engine arithmetic is literally shared, so any metric difference could only come from the
advisory layer — there is no possibility of run-to-run variation confusing the comparison.

`apply_advisory_layer` deep-copies each decision, skips anything not in `{EXCEPTION,
REVIEW_REQUIRED, UNRESOLVED}`, and for the rest attaches a single non-financial `advisory` key
holding `provider`, `priority`, `certainty`, `likely_cause`, `suggested_action`,
`investigation_steps`, `ai_confidence` and `advisory_only: True`. AI failures are fail-open: the
decision passes through untouched and a failure counter increments.

### The integrity guard

`assert_decisions_unchanged` raises `AdvisoryIntegrityError` if the decision count changes or if a
sorted signature over `("record_id", "pair_type", "status", "matched_with", "exception_type",
"amount_diff", "confidence")` differs between modes.

The module docstring frames this as a runtime re-check of a structural property: `AIAssistResult`
carries no decision, status, amount or counterpart field, so it cannot alter reconciliation
arithmetic. The guard exists to catch a future refactor that changes that.

### Verdict direction is explicit

Higher is better for precision, recall, f1, auto-resolve precision and exception-type accuracy. Lower
is better for false positives and false negatives. **Review-required rate is neutral** — as the code
comments, "a lower review rate is not automatically better: review is the safe outcome." A system
that reviewed less by guessing more would look better on a naive scoreboard.

### Result: zero measured delta

At 1,054 records / 816 labels, every compared metric is identical between modes:

| Metric | Baseline | Hybrid | Delta |
|---|---|---|---|
| Precision | 1.0000 | 1.0000 | 0.0000 |
| Recall | 1.0000 | 1.0000 | 0.0000 |
| Auto-resolve precision | 1.0000 | 1.0000 | 0.0000 |
| False positives | 0 | 0 | 0 |
| False negatives | 0 | 0 | 0 |
| Review-required | 146 | 146 | 0 |
| Review-required rate | 0.1789 | 0.1789 | 0.0000 |
| Exception-type accuracy | 0.9580 | 0.9580 | 0.0000 |

306 of 306 exceptions received advisory analysis with 0 AI failures. Cost: `0.518s` → `0.731s`, about
41% slower on the mock provider.

**The AI improves no measured metric — zero improvement, zero regression.** That is the designed
outcome, since the advisory layer was never given a lever that could move accuracy. It is reported
plainly rather than buried, because the alternative — implying gains the harness cannot demonstrate —
would be dishonest.

One caveat about the report's own output: the `decisions_changed` counter it prints as `0` is never
incremented by any code path, so its value is structurally guaranteed rather than observed. The real
enforcement is `assert_decisions_unchanged`, which raises instead of counting.

## Running it

From `backend/`:

```bash
python -m scripts.run_evaluation                      # ~62 records
python -m scripts.run_evaluation --cycles 34          # ~1,054 records
python -m scripts.run_comparison
python -m scripts.run_comparison --cycles 34 --provider mock
```

Both accept `--seed` (default `90210`) and `--cycles` (default `2`). `run_comparison` also takes
`--provider`; the CLI does not constrain the value, so an invalid one fails inside provider
construction.

Programmatic entry points: `app.evaluation.metrics.run_evaluation`, `evaluate_dataset`,
`score_results`, and `app.evaluation.comparison.run_comparison`, `compare_modes`.

Results are reproducible: the same seed and cycle count produce identical numbers.
