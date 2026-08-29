# API Reference

`RAZORZ API`, version `0.8.0`. Interactive docs at `/docs`, schema at `/openapi.json`.

Every route requires a credential except `GET /health`, `POST /auth/login`,
`POST /integrations/razorpay/webhook` and the docs paths — see [SECURITY.md](SECURITY.md). Send
either `Authorization: Bearer <session token>` or `X-API-Key: <key>`.

**Amounts are strings** in every request and response (`"1234.50"`), never JSON numbers.

## Conventions

List endpoints take `limit` (1–500, default 50) and `offset` (≥ 0) and return
`{"items": [...], "limit": n, "offset": n}`. Error bodies are
`{"detail": {"message": "...", "code": "..."}}`, except the three `404`s and the review `409`, which
return `detail` as a plain string.

## Health

### `GET /health` — public

```json
{
  "status": "ok",
  "service": "razorz-backend",
  "database": { "ok": true, "dialect": "postgresql", "host": "..." },
  "ai_provider": "mock",
  "auth": { "required": true, "scheme": "api_key" },
  "razorpay": { "configured": true, "mode": "test" }
}
```

**This route always returns HTTP 200**, even when the database is unreachable — failure is signalled
by `status: "degraded"` and `database.ok: false`. A load balancer configured to watch only the status
code will consider a broken instance healthy; parse the body instead.

## Authentication

### `POST /auth/login` — public

Body: `email` (3–254 chars), `password` (1–200 chars).

```json
{
  "token": "<opaque token>",
  "token_type": "bearer",
  "expires_at": "2026-08-30T06:21:00+00:00",
  "user": { "id": 1, "email": "ops@example.com", "full_name": "Ops", "role": "admin", "is_active": true, "last_login_at": null }
}
```

`401` with code `invalid_credentials` for a wrong password *and* for an unknown email — the message is
identical by design so the endpoint cannot enumerate accounts. Sessions last 12 hours.

### `POST /auth/logout`

No body; revokes the presented bearer token. Returns `logged_out` and `session_revoked`. An API-key
caller has no session, so `session_revoked` is `false`.

### `GET /auth/me`

Returns `authenticated`, `user` (the user object, or `null` for an API-key caller) and `auth_method`
(`"session"` or `"api_key"`).

## Reconciliation

### `POST /reconciliation/run`

Body: `records`, a list of:

| Field | Type | Default |
|---|---|---|
| `source` | string | `"synthetic"` |
| `record_type` | string | *required* — order, payment, settlement, refund, fee |
| `record_id` | string | *required* |
| `reference` | string | `""` |
| `payment_reference` | string | `""` |
| `amount` | string | `"0.00"` |
| `date` | string | `""` |
| `customer` | string | `""` |
| `metadata` | object | `{}` |

Returns `summary`, `results`, `persisted`, `run_id`. `summary` carries `run_id`, `total`, `matched`,
`exceptions`, `review_required`, `unresolved` and `match_rate`.

`record_type` is **not** enum-validated, so an unrecognized value passes request validation and is
silently ignored by the engine's partitioning step.

### `POST /ingestion/generate`

Body: `records` (**50–10000**, default 50), `seed` (default 42). Same response shape as
`/reconciliation/run`. Note the lower bound is 50, not 1 — a smaller batch is rejected with `422`.

### `GET /reconciliation/summary`

Six keys: `total_records`, `matched`, `exceptions`, `review_required`, `unresolved`, `match_rate`.
`matched` combines `MATCHED` and `AUTO_RESOLVED`.

**Scoped to the latest run only.** With no runs recorded, all six are zero. This scope differs from
`GET /exceptions` — see [the exception scoping gap](DATABASE.md#the-exception-scoping-gap).

### `GET /reconciliation/records`

Query: `limit`, `offset`, and optional `pair_type` (`order_payment`, `payment_settlement`,
`payment_refund`, `payment_fee` — accepted as a free-form string, not validated).

Each item: `id`, `run_id`, `record_id`, `matched_with`, `pair_type`, `source_record_type`,
`related_record_type`, `order_id`, `payment_id`, `settlement_id`, `refund_id`, `fee_id`, `status`,
`confidence`, `amount_diff`, `exception_type`.

One item is **one relationship**, not one transaction.

## Exceptions

### `GET /exceptions` and `GET /exceptions/{id}`

The list is paginated; the single-record route returns the object unwrapped and `404`s with
`"exception not found"`.

Item shape: `id`, `exception_type`, `status`, `severity`, `certainty`, `confidence`, `amount`,
`description`, `evidence`, `root_cause`, `recommended_action`, `human_required`, `priority`,
`reviewer_note`, `resolved_by`, `possible_root_causes`.

**The list filters by neither run nor status**, so resolved and rejected exceptions are included.

### `POST /exceptions/{id}/resolve` and `POST /exceptions/{id}/reject`

Body: `actor` (default `"reviewer"`), `note` (**required, min 3 chars**). Returns the enriched
exception.

- `404` — unknown id
- `409` — already in the other terminal state; re-applying the same action is idempotent

Requiring a note is deliberate: a resolution without a reason is not auditable.

### `POST /exceptions/{id}/ai-assist`

Body: `mode` — `full_analysis` (default), `suggest_note`, or `investigation_steps`.

Returns `exception_id`, `mode`, `provider`, `evidence_packet`, `deterministic_confidence`,
`assistance`, `advisory_only` (always `true`) and `disclaimer`. `assistance` holds `likely_cause`,
`explanation`, `investigation_steps`, `suggested_action`, `suggested_review_note` and `ai_confidence`
(0–1).

- `404` — unknown id
- `503` — code `provider_unavailable` or `timeout`
- `422` — `invalid_response`, `unsupported_mode`, or `integrity_violation`

`ai_confidence` is the model's confidence in its own explanation and is **not** reconciliation
confidence. This endpoint cannot resolve or reject anything; see
[AI_AND_COPILOT.md](AI_AND_COPILOT.md).

## Audit

### `GET /audit`

Items: `id`, `event_type`, `entity_id`, `actor`, `action`, `new_state`, `confidence`. Newest first,
unfiltered.

Because a reconciliation run writes one event per decision, a large run can fill the first page. To
find a login or webhook event after a run, page further or raise `limit`.

## Finance Copilot

### `GET /copilot/suggestions`

Returns `suggestions` (6 example questions), `tools` (the 10 read-only tool names), `read_only`
(`true`) and a `disclaimer`.

### `POST /copilot/ask`

Body: `question` (1–1000 chars), `history` (up to 8 turns of `{role: "user" | "assistant", content}`,
each ≤ 2000 chars).

Returns `question`, `intent`, `provider`, `llm_used`, `read_only`, `refused`, `answer`, `tools_used`,
`tool_errors`, `evidence`, `disclaimer`, and `grounding_warning` when the grounding check rejected the
model's narrative. `answer` holds `answer`, `key_findings`, `data_points`, `sources_used` and
`confidence`.

- `503` — `provider_unavailable`, `timeout`
- `422` — `invalid_response`, `empty_question`, `invalid_parameters`, `copilot_failure`

A question asking for a mutation returns `200` with `refused: true` and `intent:
"mutation_refused"` — no tool runs and no LLM is called.

## Razorpay

### `GET /integrations/razorpay/status`

Returns `provider`, `mode`, `configured`, `connected`, `key_id_prefix` (truncated — never the
secret), `capabilities`, `webhooks`, `notes`, plus `error` when unconfigured or unreachable.
`capabilities` reports settlements and fees as `"partial"`, honestly reflecting Razorpay Test Mode.

### `POST /integrations/razorpay/sync`

Body: `count` (1–100, default 50). Returns `source`, `mode`, `synced`, `empty`, `counts`,
`settlement_error`, `summary`, `results`, `persisted`, `message`, and `run_id` when records were
found.

- `503` — `timeout`, `network_error`, `rate_limited`
- `422` — everything else

**A caveat on those status codes:** `not_configured`, `live_blocked`, `key_mode_mismatch`,
`auth_failed` and `api_error` all surface as `422`, which reads as a client error for what are
really server misconfigurations. Check the `code`, not the status.

`settlement_error` is populated rather than raised when only the settlement fetch fails, so a partial
sync completes instead of inventing rows.

### `POST /integrations/razorpay/webhook` — public, HMAC-authenticated

Verified by `X-Razorpay-Signature` over the raw body; deduplicated on `X-Razorpay-Event-Id`. Full
detail in [RAZORPAY.md](RAZORPAY.md).

- `401` — `invalid_signature`
- `503` — `webhook_not_configured`
- `422` — `invalid_payload`
- `500` — `processing_failed`

A duplicate delivery returns `200` with `duplicate: true, processed: false`. An unsupported event
returns `200` with `supported: false` and a reason — acknowledging rather than erroring, so the
provider stops retrying an event we will never process.
