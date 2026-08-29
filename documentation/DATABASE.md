# Database

Thirteen tables, all defined in `backend/app/models.py` as SQLModel classes. Every table has
`id: Optional[int]` as an autoincrementing primary key, and timestamp columns default to
`datetime.now(timezone.utc)`.

## Money and precision

Amount, confidence and score columns are Python `Decimal`, mapping to SQL `NUMERIC`/`DECIMAL`.
`float` is never used for money anywhere in the persistence or matching path.

No precision or scale is declared in the models, so Postgres receives unconstrained `NUMERIC`.
Convention is enforced in code rather than by the schema: repositories coerce input with
`Decimal(str(value))` — never `Decimal(float)` — and serialize with
`str(value.quantize(Decimal("0.01")))`. That is why **API responses carry amounts as JSON strings**.

The trade-off is honest: relying on convention rather than a column constraint means a future writer
that skips the helper could store more than two decimal places. Declaring
`NUMERIC(18, 2)` would make the database enforce it.

## Source record tables

Five tables share a shape: a `source_id` foreign key to `sources.id`, a unique `external_id` (the
provider's id, which makes ingestion idempotent), an indexed `reference`, a `Decimal` `amount`,
`currency` defaulting to `"INR"`, an indexed date column, and timestamps.

| Table | Class | Link column | Date column | `status` default |
|---|---|---|---|---|
| `sources` | `Source` | — | — | `"active"` |
| `orders` | `Order` | — | `order_date` | `"pending"` |
| `payments` | `Payment` | `order_reference` | `payment_date` | `"captured"` |
| `settlements` | `Settlement` | `payment_reference` | `settlement_date` | `"pending"` |
| `refunds` | `Refund` | `payment_reference` | `refund_date` | `"processed"` |
| `fees` | `Fee` | `payment_reference` | `fee_date` | *(no status column)* |

Additional columns: `orders` and `payments` carry `customer_ref`; `payments` has `payment_method`
(default `"UPI"`); `fees` has `fee_type` (default `"processing"`); `sources` has `source_type`
(synthetic, razorpay, bank) and `source_metadata`.

**Links between records are unconstrained strings, not foreign keys.** `order_reference` and
`payment_reference` are indexed `VARCHAR`. The only real foreign keys in the schema are
`sources.id` (from the five record tables) and `users.id` (from `user_sessions`).

This is deliberate: reconciliation must be able to ingest a settlement that references a payment
which has not arrived yet — that dangling reference *is* the finding
(`ORPHAN_SETTLEMENT`). A foreign key would reject the row and destroy the evidence. The cost is that
the database cannot guarantee referential integrity, so orphan detection is application logic.

## Reconciliation tables

### `reconciliation_records` — one row per relationship per run

| Column | Type | Notes |
|---|---|---|
| `run_id` | `str` | Indexed; UUID per reconciliation pass |
| `source_type`, `source_record_id` | `str` | Indexed |
| `matching_key` | `str` | Indexed |
| `matched_with` | `Optional[str]` | Indexed; the counterpart id, `NULL` when unmatched |
| `pair_type` | `Optional[str]` | Indexed; default `"order_payment"` |
| `source_record_type`, `related_record_type` | `Optional[str]` | Indexed |
| `status` | `str` | Indexed; default `"UNRESOLVED"` |
| `confidence`, `amount_diff` | `Decimal` | |
| `exception_type` | `Optional[str]` | Indexed |
| `evidence` | `Optional[str]` | JSON serialized to text, not a JSON column |

### `match_candidates`

Runner-up matches retained for auditability: `run_id`, `source_record_id`,
`candidate_record_id`, `score` (`Decimal`), `rank` (default `1`), and `features` as JSON text. Keeping
rejected candidates is what allows a reviewer to see *why* one payment won over another.

### `exceptions` — class `ExceptionRecord`

Note the class and table names differ; the table is `exceptions`.

`exception_type` (indexed), `severity` (default `"medium"`), `status` (indexed, default `"OPEN"`),
`certainty` (default `"UNKNOWN"`), `confidence`, nullable `amount`, `description`, `evidence`,
`root_cause`, `recommended_action`, `reviewer_note`, `resolved_by`, timestamps.

An exception row is created only when a decision's status is `EXCEPTION`, `REVIEW_REQUIRED` or
`UNRESOLVED`.

### The exception scoping gap

**`exceptions` has no `run_id` column.** The consequence is visible in the UI:

- `summary()` resolves the newest `run_id` and counts only that run.
- `list_exceptions()` filters by neither run **nor** status, so it returns every exception row ever
  created — open, resolved and rejected alike.

So a dashboard reading "1 exception" beside an exceptions page listing 49 is not a bug in either
query; they answer different questions. Closing it properly means deciding whether an unresolved
break from an earlier batch should still count as open (it arguably should, since exceptions persist
until a human clears them), and then either adding `run_id` to the table or scoping the dashboard to
all open exceptions.

## Audit table

`audit_events` is append-only: `event_type`, `entity_type`, `entity_id` (all indexed), `actor`
(default `"system"`), optional `confidence`, `previous_state`, `new_state`, `action`, `evidence`
(JSON text), `details`.

Event types actually written: `reconciliation_decision`, `human_review`, `ai_assistance`,
`auth_event`, `webhook_event`, `copilot_query`.

Two operational notes. A reconciliation run writes **one row per decision**, so a 159-record run adds
~159 rows — with `GET /audit?limit=100` returning newest-first, a run's rows can fill an entire page
and push login or webhook events out of view. And the **polling Razorpay sync writes no audit event
of its own**, so "synced from Razorpay" leaves no direct trace; only the resulting decisions appear.

## Auth tables

**`users`** — unique indexed `email` (normalized to lowercase), `full_name`, `password_hash`, indexed
`role` (`admin` | `reviewer` | `viewer`, default `reviewer`), indexed `is_active`, `last_login_at`.
Class docstring: "Only a derived password hash is stored, never the password."

**`user_sessions`** — `user_id` foreign key, unique indexed `token_hash`, indexed `expires_at`,
`last_seen_at`, `revoked_at`. Docstring: "Stores a hash of the token so a DB leak cannot log in."

Only the SHA-256 hash of a session token is persisted. Logout sets `revoked_at`, making revocation
real rather than client-side forgetting. See [SECURITY.md](SECURITY.md).

## Webhook table

`webhook_events` — indexed `provider` (default `"razorpay"`), **unique** indexed `event_id`, indexed
`event_type`, indexed `entity_id`, `payload_digest`, indexed `status` (default `"RECEIVED"`),
`records_ingested`, `run_id`, `error_code`, `received_at`, `processed_at`.

The unique `event_id` is the idempotency key. The docstring is explicit that the table "stores no
secrets: no signature, no headers, no request body — only a digest."

## Engine, bootstrap and RLS

`backend/app/db.py`:

- **Engine cache** — one engine per distinct URL per process, with `pool_pre_ping=True`. SQLite gets
  `check_same_thread: False`, and in-memory SQLite additionally gets `StaticPool` so the database
  survives across connections (this is what makes the test suite work).
- **Bootstrap memoization** — `_bootstrapped` tracks URLs whose DDL pass already ran, because
  "repositories construct freely, so without this the DDL/RLS pass would repeat on every single
  request."
- **`_ensure_multi_record_columns`** — best-effort `ADD COLUMN` for `payment_reference` on
  `settlements`/`fees` and `pair_type`/`source_record_type`/`related_record_type` on
  `reconciliation_records`. Introspects via `PRAGMA table_info` on SQLite, `information_schema` on
  Postgres.
- **`lock_public_tables`** — on Postgres only, enables row-level security on all 13 tables and
  revokes access from the Supabase Data API roles `anon` and `authenticated`. It is a no-op on
  SQLite.

**RLS is enabled with no policies.** That is the intent — the API connects as the table owner and
bypasses RLS, while the Supabase Data API roles get nothing, so the tables are not reachable through
the public REST endpoint. Anything that connects as a non-owner role will see zero rows.

## Repositories

| Repository | Responsibility |
|---|---|
| `ReconciliationRepository` | Persist results; summary and record listings; exception review; all audit writers |
| `RecordRepository` | Upsert normalized source records; read-only financial totals and linkage analysis |
| `UserRepository` | Users, password verification, session issue/resolve/revoke |
| `WebhookRepository` | `get` / `claim` / `complete` on the delivery log |

Each takes an optional `database_url` and calls `create_db_and_tables` on construction, which is why
the bootstrap memoization matters.

Which methods scope to a run:

- **Latest run only** — `summary()`, `pair_type_breakdown()`, and `list_recent()` when
  `latest_run_only=True` (the default).
- **No run or status filter** — `list_exceptions()`, `list_audit()`, and every `RecordRepository`
  analysis method (`financial_totals`, `unsettled_payments`, `settlement_linkage`, `link_coverage`).

That split is the root of the reporting mismatch described above, and it is why the Copilot's tools
state their scope explicitly in their responses.

## No enums

Every status, type and severity is a plain `str` column. There are no Python `Enum` classes and no
database `CHECK` constraints; the valid sets live as module constants
(`ROLES`, `VALID_PAIR_TYPES`, `TAXONOMY` keys, `HARD_EXCEPTIONS`, `REVIEW_EXCEPTIONS`). Validation is
by convention, so an invalid literal written by new code would persist silently. The
exhaustive value lists are in [RECONCILIATION.md](RECONCILIATION.md#status-and-type-vocabulary).
