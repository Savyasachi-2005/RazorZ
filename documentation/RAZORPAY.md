# Razorpay Integration

Razorpay Test Mode is supported through two independent paths: **polling** (pull on demand) and
**webhooks** (push on event). Both funnel into the same normalization and reconciliation pipeline, and
polling remains fully functional as a fallback if webhooks are not configured.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `RAZORPAY_KEY_ID` | `""` | Basic-auth username, e.g. `rzp_test_xxxxxxxx` |
| `RAZORPAY_KEY_SECRET` | `""` | Basic-auth password — **secret** |
| `RAZORPAY_MODE` | `test` | `test` requires an `rzp_test_` key id |
| `RAZORPAY_BASE_URL` | `https://api.razorpay.com/v1` | API host |
| `RAZORPAY_TIMEOUT_SECONDS` | `20` | HTTP timeout |
| `RAZORPAY_ALLOW_LIVE` | `false` | Must be `true` for any non-test mode |
| `RAZORPAY_WEBHOOK_SECRET` | `""` | HMAC signing secret — **secret**, different from the key secret |
| `RAZORPAY_WEBHOOK_RECONCILE` | `false` | Run a full reconciliation after each ingested webhook |

`assert_ready` enforces three preconditions before any call: keys present (`not_configured`),
non-test mode requires `RAZORPAY_ALLOW_LIVE` (`live_blocked`), and test mode requires an `rzp_test_`
prefix (`key_mode_mismatch`).

That last check is worth its own sentence: it prevents a live key from being used while the system
believes it is in test mode. Two independent guards must both be wrong before live data is touched.

## Normalization

`mapper.py` converts provider payloads to the internal record shape.

**Paise to rupees** — every amount is `Decimal(value) / 100`, quantized to `0.01`. Integer paise
divided as `Decimal` means no float ever touches the conversion.

**Only reconcilable payments enter matching** —
`RECONCILABLE_PAYMENT_STATUSES = {"captured", "authorized", "refunded"}`. Payments in `created` or
`failed` are dropped, because a failed attempt against an order is not money and its presence made
order↔payment matching ambiguous.

**Customer is deliberately left empty** on both orders and payments, with the email and contact
stored in metadata instead. The scoring function compares customers by exact equality, so filling the
payment side with an email while orders had an empty string made every pair mismatch on a 20%-weighted
feature and pushed correct matches down to `REVIEW_REQUIRED`. Empty on both sides compares equal.
This was a real bug, fixed in the adapter without touching the engine.

**A fee record is derived** from `payment.fee` only when the paise value is present and greater than
zero — no fee is invented.

## Polling

`POST /integrations/razorpay/sync`, body `count` (1–100, default 50).

Fetches orders, payments and refunds sequentially, then settlements inside a `try`. **A settlement
failure does not fail the sync** — the error is captured into `settlement_error` and the run
continues with what was retrieved. Test Mode settlements are frequently empty or batch-level, and
inventing rows to fill the gap would be worse than reporting the gap.

With zero records the response short-circuits with `empty: true`, `persisted: false` and a zeroed
summary rather than recording an empty run.

`GET /integrations/razorpay/status` reports connectivity and capabilities, listing settlements and
fees as `"partial"` — an honest reflection of Test Mode. `key_id_prefix` is truncated to 12
characters and the secret is never returned.

## Webhooks

`POST /integrations/razorpay/webhook` — public in the API-key sense, authenticated by signature.

### Verification

HMAC-SHA256 over the **raw request bytes**, compared with `hmac.compare_digest` against
`X-Razorpay-Signature` (case-insensitive lookup). Raw bytes matter — re-serializing the JSON could
reorder keys and invalidate a valid signature.

No secret configured → `503 webhook_not_configured`, not silent acceptance.

### Processing order

1. **No secret** → audit, `503`.
2. **Invalid signature** → audit `invalid_signature`, `401`, nothing ingested.
3. **Unparseable payload** → audit, `422 invalid_payload`.
4. **Claim the event id.** A duplicate returns `200` with `duplicate: true, processed: false`.
5. **Unsupported event** → marked `IGNORED`, returned `200` with `supported: false` and a reason.
6. **Normalize and persist** via the same upsert path polling uses.
7. **Reconcile only if `RAZORPAY_WEBHOOK_RECONCILE=true`** and records were ingested — delegating to
   the same `sync_and_reconcile` routine.
8. **Failure after verification** → row marked `FAILED`, `500 processing_failed`.

Acknowledging an unsupported event with `200` is intentional: a `4xx` would make Razorpay retry
forever an event we will never process.

### Supported events

| Event | Payload containers used |
|---|---|
| `order.paid` | order, payment |
| `payment.captured` | payment |
| `payment.authorized` | payment |
| `refund.created` | refund |
| `refund.processed` | refund |
| `settlement.processed` | settlement |

Explicitly ignored with a stated reason: `payment.failed`,
`order.notification.delivered`, `payment.downtime.started`, `payment.downtime.resolved`.

### Idempotency, at three levels

1. **Delivery** — the key is the `X-Razorpay-Event-Id` header, or `digest:<sha256 of body>` when the
   header is absent. `WebhookRepository.claim` performs a select-then-insert against the unique
   `event_id` column and reports whether the row was newly created.
2. **Record** — source records are upserted by `external_id`, so even if a delivery were processed
   twice no second financial row could appear.
3. **Within one event** — duplicate `record_id`s in a single payload are deduplicated.

A delivery previously marked `FAILED` **may** be retried; one marked `PROCESSED` or `IGNORED` may
not. That distinction is what makes retries safe without making duplicates possible.

### Statuses and audit

`WebhookEvent.status` is one of `RECEIVED`, `PROCESSED`, `IGNORED`, `FAILED`.

Audit `action` values: `rejected`, `duplicate`, `received`, `ignored`, `processed`, `failed`. Error
codes recorded: `webhook_not_configured`, `invalid_signature`, `invalid_payload`,
`unsupported_event`, `processing_failed`, `no_reconcilable_record`.

**No signature value, secret, header set or request body is ever stored** — only a payload digest.

## Reconciliation rules are untouched

The integration only produces normalized records. It contains no matching logic, no thresholds and no
status decisions, and the engine imports nothing from `integrations/`. A provider quirk can therefore
change *what* is reconciled but never *how*.

## Limitations

- **Test Mode settlements** are often empty or batch-level, so `payment_settlement` coverage is
  naturally sparse. `SETTLEMENT_MISSING` findings against Test Mode data usually reflect the sandbox,
  not a real break.
- **Webhooks ingest; they do not reconcile per event.** With `RAZORPAY_WEBHOOK_RECONCILE=true` the
  system runs a *full-dataset* pass, not an incremental one — correct, but heavier than necessary.
- **The polling sync writes no audit event of its own.** Only the resulting decisions appear in the
  trail, so "synced from Razorpay" is not directly visible.
- **Webhook deliveries are not surfaced in the UI.** The `webhook_events` table is queryable only
  directly.
- **No signature-timestamp / replay window check.** Idempotency prevents duplicate *processing*, but
  an attacker holding a validly signed old body would still be accepted as a first-time delivery if
  its event id was never seen.
