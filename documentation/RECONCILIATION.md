# Reconciliation Engine

The deterministic core. Given normalized records it decides which pairs relate, by how much they
differ, and what status each relationship gets. No AI, no network, no database — the functions in
`backend/app/reconciliation/` are pure.

## Pipeline

`run_reconciliation` (`backend/app/services/reconciliation_service.py:102`):

1. **Normalize** — `_normalize_record` coerces `amount` to `Decimal`, strips `reference`,
   `customer` and `payment_reference`, and defaults `record_type` to `"unknown"`. For settlements and
   fees, a missing `payment_reference` falls back to `reference`, because providers often put the
   payment id there.
2. **Partition** by record type into orders, payments, settlements, refunds, fees.
3. **Match, layer by layer**, in fixed order:
   - `_reconcile_orders_payments` — always runs
   - `_reconcile_payments_settlements` — only if settlements are present or expected
   - `_reconcile_payments_refunds`
   - `_reconcile_payments_fees`
4. **Decide** — every branch ends at `decide_status`, producing `(status, confidence)`.
5. **Persist** — source records are upserted, then results are written under one `run_id` with match
   candidates, exception rows and audit events.

A payment consumed by an order↔payment match cannot be matched to another order in that layer. The
settlement, refund and fee layers may each reference the same payment, which is correct — one payment
legitimately has a settlement *and* a fee.

## Order ↔ Payment: the only scored relationship

Attempts run in order; the first that applies wins.

**1. Exact identity match** — confidence `0.99`, status `MATCHED`. Requires all of:

- the payment is unconsumed
- `payment.reference == order.reference`, and that reference is **non-empty**
- `payment.amount == order.amount` (exact `Decimal` equality)
- `payment.customer == order.customer`

Date is deliberately not part of identity. A payment captured a day after the order is the same
money.

**2. Same identity, ambiguous** — reference and customer match on more than one payment →
`AMBIGUOUS_MATCH` at `0.72`, with `matched_with` left `NULL`. The engine refuses to guess between
two candidates that look equally valid.

**3. Same identity, wrong amount** — exactly one identity match but a non-zero difference →
`AMOUNT_MISMATCH` at `0.82`. Identity is established, so this is a value break rather than a
matching failure.

**4. Fuzzy scoring** — no identity match, so score every unconsumed payment and take the top three.
If the best two scores are within `0.05` of each other and the best is at least `0.70`, the result is
`AMBIGUOUS_MATCH` rather than a match. Otherwise `decide_status` maps the score.

**5. `PAYMENT_MISSING`** at `0.40` — an order with no acceptable candidate.

**6. `ORPHAN_PAYMENT`** at `0.35` — a second pass over payments no result referenced.

### Scoring weights

`backend/app/reconciliation/scoring.py`, all `Decimal`, summing to `1.00`:

| Feature | Weight | How it is computed |
|---|---|---|
| `reference` | `0.35` | `1.00` if equal and non-empty; `0.00` if either is empty; otherwise common **leading prefix** length ÷ longer length |
| `amount` | `0.30` | `1.00 - abs(diff) / max(abs(a), abs(b))`, floored at `0.00`; `1.00` when both are zero |
| `customer` | `0.20` | `1.00` if equal and non-empty, else `0.00` |
| `date` | `0.15` | same day `1.00`; ≤2 days `0.85`; ≤7 `0.60`; ≤14 `0.30`; beyond `0.00`; unparseable `0.50` |

Two consequences worth knowing. Reference similarity compares *prefixes only* and stops at the first
mismatching character, so `order_A1` vs `order_B1` scores on the shared `order_` prefix — good for
provider-prefixed ids, weak for ids that differ early and match later. And customer similarity is
exact-match only, so an empty customer field scores `0.00`; this is why the Razorpay adapter
deliberately leaves `customer` empty on *both* sides of a pair rather than filling one side with an
email.

## The other three relationships: dictionary joins

Settlements, refunds and fees are keyed by payment id — no scoring, no fuzzy matching.

**Payment ↔ Settlement**: no settlement → `SETTLEMENT_MISSING` (`0.40`); more than one →
`DUPLICATE_SETTLEMENT` (`0.55`); amount differs → `SETTLEMENT_AMOUNT_MISMATCH` (`0.82`); otherwise
`0.99`. A settlement whose key matches no known payment → `ORPHAN_SETTLEMENT` (`0.35`). Payments
whose metadata explicitly says `expects_settlement: false` are skipped.

**Payment ↔ Refund**: expected but absent → `REFUND_MISSING` (`0.40`); refunds totalling more than
the captured amount → `REFUND_EXCESSIVE` (`0.50`) with the excess as `amount_diff`; several refunds
within the captured amount → `MULTIPLE_REFUNDS` (`0.75`); one clean refund → `0.99`; unmatched refund
→ `ORPHAN_REFUND` (`0.35`).

**Payment ↔ Fee**: expected fee is `metadata["expected_fee_amount"]` when supplied, otherwise 2% of
the payment (`expected_fee_amount`, quantized to `0.01`). No fee → `FEE_MISSING` (`0.40`); any
non-zero difference → `FEE_DIFFERENCE` (`0.82`); match → `0.99`; unmatched fee → `FEE_UNEXPECTED`
(`0.35`).

**There is no amount tolerance in any layer.** Comparisons are `> Decimal("0")`. One paisa is a
break. Appropriate for reconciliation, but it means a provider that rounds differently will generate
exceptions rather than absorb them.

## Decision thresholds

`ReconciliationThresholds` (`backend/app/config.py:39`), environment-overridable:

| Threshold | Env var | Default |
|---|---|---|
| `auto_resolve` | `RECON_AUTO_RESOLVE` | `0.99` |
| `auto_resolve_warning` | `RECON_AUTO_RESOLVE_WARNING` | `0.90` |
| `human_review` | `RECON_HUMAN_REVIEW` | `0.70` |

`decide_status` (`backend/app/reconciliation/decisions.py:35`) applies rules in strict precedence:

1. `exact_match` → `MATCHED`, confidence at least `0.99`
2. `ambiguous` → `REVIEW_REQUIRED`, confidence capped at `0.70`
3. type in `HARD_EXCEPTIONS` → `EXCEPTION`, confidence capped at `0.69`
4. type in `REVIEW_EXCEPTIONS` → `REVIEW_REQUIRED`, confidence clamped to `[0.70, 0.90]`
5. otherwise by score: `≥ 0.90` → `AUTO_RESOLVED`; `≥ 0.70` → `REVIEW_REQUIRED`; below → `UNRESOLVED`

An exception type always outranks a high score. A `PAYMENT_MISSING` cannot be auto-resolved no matter
what the numbers say, which is the point of separating routing from scoring.

**A redundancy worth documenting:** the branches for `>= auto_resolve` (0.99) and
`>= auto_resolve_warning` (0.90) both return `AUTO_RESOLVED`, so with default thresholds `0.99` is not
a distinct tier and `MATCHED` is reachable *only* via `exact_match=True`. The behaviour is correct
but one branch is dead. Also, thresholds are frozen dataclass defaults evaluated at import, so
changing the env vars requires a restart.

## Status and type vocabulary

**Relationship statuses** (5): `MATCHED`, `AUTO_RESOLVED`, `REVIEW_REQUIRED`, `EXCEPTION`,
`UNRESOLVED`.

**Exception lifecycle statuses** (3): `OPEN` → `RESOLVED` or `REJECTED`. `review_exception` rejects any
action other than resolve/reject, is idempotent if already at the target state, and refuses to move
between two terminal states.

**Pair types** (4): `order_payment`, `payment_settlement`, `payment_refund`, `payment_fee`.

**Exception types** — 18 entries in `TAXONOMY` (`backend/app/exceptions/intelligence.py:8`), each
carrying `meaning`, `severity`, `certainty`, `human_required`, `ai_useful`, `recommended_action` and
`root_causes`. All 18 have `human_required: True`.

| Type | Severity | Certainty |
|---|---|---|
| `PAYMENT_MISSING` | high | CONFIRMED |
| `AMOUNT_MISMATCH` | high | CONFIRMED |
| `ORPHAN_PAYMENT` | medium | CONFIRMED |
| `AMBIGUOUS_MATCH` | medium | PROBABLE |
| `DATE_MISMATCH` | low | PROBABLE |
| `SETTLEMENT_MISSING` | high | CONFIRMED |
| `SETTLEMENT_AMOUNT_MISMATCH` | high | CONFIRMED |
| `ORPHAN_SETTLEMENT` | medium | CONFIRMED |
| `DUPLICATE_SETTLEMENT` | high | CONFIRMED |
| `REFUND_MISSING` | high | CONFIRMED |
| `ORPHAN_REFUND` | medium | CONFIRMED |
| `REFUND_EXCESSIVE` | high | CONFIRMED |
| `REFUND_MISMATCH` | high | CONFIRMED |
| `MULTIPLE_REFUNDS` | medium | PROBABLE |
| `FEE_MISSING` | medium | CONFIRMED |
| `FEE_DIFFERENCE` | medium | CONFIRMED |
| `FEE_UNEXPECTED` | medium | CONFIRMED |
| `UNKNOWN_EXCEPTION` | medium | UNKNOWN |

**Routing sets.** `HARD_EXCEPTIONS` (never auto-resolve): `PAYMENT_MISSING`, `ORPHAN_PAYMENT`,
`ORDER_MISSING`, `SETTLEMENT_MISSING`, `ORPHAN_SETTLEMENT`, `DUPLICATE_SETTLEMENT`, `REFUND_MISSING`,
`ORPHAN_REFUND`, `REFUND_EXCESSIVE`, `FEE_MISSING`, `FEE_UNEXPECTED`. `REVIEW_EXCEPTIONS` (identity
linked but financially unsafe): `AMOUNT_MISMATCH`, `DATE_MISMATCH`, `AMBIGUOUS_MATCH`,
`SETTLEMENT_AMOUNT_MISMATCH`, `REFUND_MISMATCH`, `MULTIPLE_REFUNDS`, `FEE_DIFFERENCE`.

**Three loose ends, stated plainly:** `ORDER_MISSING` appears in `HARD_EXCEPTIONS` but has no
taxonomy entry and is never emitted, so it would classify as `UNKNOWN_EXCEPTION` if it ever were.
`DATE_MISMATCH` and `REFUND_MISMATCH` have full taxonomy entries and routing but the engine never
produces them.

## Severity, certainty and priority

Severity is written twice. The repository sets `high` when `amount_diff >= 500` else `medium`, then
`apply_classification` overwrites severity with the taxonomy value whenever the stored value is empty
**or literally `"medium"`**. So an amount-driven `high` survives, but an amount-driven `medium` is
always replaced. Effectively the taxonomy wins except for large-value overrides.

Priority is derived, not stored: `P1` when amount ≥ 500 or the type is in `HIGH_IMPACT_TYPES`; `P3`
for a `DATE_MISMATCH` under 100; otherwise `P2`.

## Decimal discipline

All monetary arithmetic is `Decimal`: normalization, every difference and sum, all scoring weights,
the 2% fee expectation, and every stored column.

For completeness, the three places `float` appears near the decision path — none of which touch a
monetary amount:

1. `engine.py:59` puts `float(confidence)` in the result dict; persistence re-parses it via
   `Decimal(str(...))`.
2. `engine.py:78` puts `float(pair["score"])` into candidate dicts, so the ambiguity comparison
   `abs(best - second) < 0.05` is float arithmetic. Scores are quantized to two decimals beforehand,
   so in practice it is exact, but the comparison itself is not `Decimal`. This is the one genuine
   float step in a decision.
3. `match_rate` is a float ratio for reporting.

`amount_diff` travels as a string and is re-parsed to `Decimal`, so no float is involved.
