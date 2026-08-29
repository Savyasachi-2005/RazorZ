# Architecture

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI 0.115 (`RAZORZ API`, version `0.8.0`) |
| ORM / models | SQLModel 0.0.22 over SQLAlchemy |
| Database | PostgreSQL via `psycopg` 3.3 in deployment; SQLite for tests and local default |
| Frontend | React 19, TypeScript 5.9, Vite 7, Tailwind CSS 3.4 |
| AI | Pluggable provider — mock (default), Google Gemini, or any OpenAI-compatible HTTP endpoint |
| Tests | pytest 8.3 (292 tests) |

Python 3.13 and Node 22 are the versions exercised in [CI](DEVELOPMENT.md#continuous-integration).

## Request flow

```
Browser (React SPA)
  │  fetch with Authorization: Bearer <session token>
  ▼
Vite dev proxy (dev only) ──────────► FastAPI
                                        │
                    require_api_key (app-wide dependency)
                                        │
                              route handler in app/main.py
                                        │
                     ┌──────────────────┼──────────────────┐
                     ▼                  ▼                  ▼
              services/          integrations/         copilot/  ai/
        reconciliation_service   razorpay             (read-only) (advisory)
                     │                  │                  │
                     ▼                  ▼                  ▼
                        repositories/  ──►  SQLModel  ──►  DB
```

Every route is protected by default because the auth dependency is installed on the application
rather than on individual routes — a new endpoint is closed unless explicitly exempted. See
[SECURITY.md](SECURITY.md).

## Directory map

```
backend/app/
  main.py              All HTTP routes, request/response models, exception→status mapping
  config.py            Frozen settings dataclass; loads .env then backend/.env
  db.py                Engine cache, one-time schema bootstrap, Postgres RLS lock
  models.py            13 SQLModel tables
  security.py          require_api_key: session token or API key; PUBLIC_PATHS
  auth/                Password hashing (PBKDF2), login/logout/me, admin bootstrap
  reconciliation/      engine.py, scoring.py, decisions.py — the deterministic core
  exceptions/          intelligence.py — 18-type taxonomy, severity/certainty/priority
  ai/                  Advisory assist: service, schemas, prompts, providers/
  copilot/             Read-only Q&A: deterministic router, 10 tools, grounding check
  integrations/razorpay/  client, mapper, polling service, webhook verification
  repositories/        Persistence: reconciliation, record, user, webhook
  services/            reconciliation_service.py — orchestration + serialization
  evaluation/          Held-out dataset, metrics, baseline-vs-hybrid comparison
backend/scripts/       run_evaluation, run_comparison, create_user, enable_rls
backend/tests/         292 tests
frontend/src/          pages/, layout/, state/, components/ui/, api.ts
```

## Layering rules the code actually enforces

**The engine is pure.** `reconciliation/engine.py` takes a list of normalized dicts and returns a
list of decision dicts. It performs no I/O, touches no repository, and imports nothing from
`integrations/` or `ai/`. Consequently a provider integration can never change a matching outcome
except by changing the records it submits.

**Adapters normalize at the edge.** `integrations/razorpay/mapper.py` converts provider payloads
into the internal record shape — paise to rupee `Decimal`, provider ids into `external_id`. The
engine never sees a Razorpay-specific field.

**Repositories own persistence, not decisions.** `record_repository.py` states in its module
docstring that reconciliation decisions never depend on it, and `webhook_repository.py` likewise:
its table "exists only to make processing idempotent."

**The evaluation package is isolated.** `app/evaluation/__init__.py` states the invariant:
"Nothing in `app.evaluation` is imported by the reconciliation engine, the Copilot or the API — the
evaluation dataset must never influence inference." Held-out data cannot leak into behaviour
because nothing in the serving path can import it.

**AI and Copilot are terminal consumers.** Both read committed state and return text. Neither
writes a financial field.

## The reconciliation run

1. `POST /reconciliation/run`, `POST /ingestion/generate`, or a Razorpay sync supplies records.
2. `run_reconciliation` (`services/reconciliation_service.py:102`) ensures schema, then calls
   `reconcile_records`.
3. The engine normalizes and partitions records by type, then runs four relationship layers in
   fixed order: order↔payment, payment↔settlement, payment↔refund, payment↔fee.
4. Source records are upserted by `external_id` — inside `try/except` because "persistence is
   additive and must never block a reconciliation run."
5. Results are persisted under a single `run_id` (a UUID) with match candidates, exception rows
   for non-clean statuses, and one audit event per decision.

Full detail in [RECONCILIATION.md](RECONCILIATION.md).

## Two ingestion paths

**Polling** (`POST /integrations/razorpay/sync`) fetches orders, payments, refunds and settlements,
normalizes them, and runs a full reconciliation pass.

**Webhooks** (`POST /integrations/razorpay/webhook`) verify an HMAC-SHA256 signature over the raw
body, deduplicate on the delivery event id, normalize, and persist. Reconciliation is *not* run per
event unless `RAZORPAY_WEBHOOK_RECONCILE=true`, in which case it delegates to the same polling
routine. Polling remains fully functional as a fallback. See [RAZORPAY.md](RAZORPAY.md).

## Schema bootstrap instead of migrations

There is no Alembic. `create_db_and_tables` runs `SQLModel.metadata.create_all`, applies a
best-effort `ADD COLUMN` pass for columns added after the original schema, and locks tables with
Postgres RLS. It is memoized per database URL per process, because repositories construct freely and
the DDL pass would otherwise repeat on every request.

The honest consequence: this suits a demo and is not a migration strategy. Column *renames*, type
changes and drops are unsupported, and the first request after a restart pays a one-time cost of
roughly 10–25 seconds against remote Postgres.

## Trade-offs worth knowing

**Scoring only applies to order↔payment.** The other three relationships are dictionary joins on the
payment id. Amount comparison there is exact (`diff > 0`), so there is no fuzzy tolerance for
settlements, refunds or fees.

**Amounts have no tolerance band anywhere.** `amount_similarity` reduces the score for any non-zero
difference, and the engine tests `> Decimal("0")` exactly. A one-paisa discrepancy is a real break.
This is intentional for a reconciliation tool but means noisy provider rounding would surface as
exceptions.

**`exceptions` has no `run_id` column,** so exception listings cannot be scoped to a run. The
dashboard counts the latest run while the exceptions list spans all runs, which makes the two
numbers legitimately disagree. This is a known reporting gap, not a data error — see
[DATABASE.md](DATABASE.md#the-exception-scoping-gap).

**Settings are read once at import.** `Settings` is a frozen dataclass, so every environment change
requires a process restart, including API key rotation.
