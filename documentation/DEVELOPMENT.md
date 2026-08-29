# Development

## Requirements

- **Python 3.13** (3.13.2 is the version used locally and in CI)
- **Node 22** — Vite 7 requires `^20.19 || >=22.12`
- PostgreSQL optional; SQLite is the default and is what the test suite uses

## Setup

```bash
# from the repository root
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r backend/requirements.txt

cd frontend
npm ci
```

Copy `.env.example` to `.env` and adjust. Nothing is required to start: with no `DATABASE_URL` the
backend uses `sqlite:///./razorz.db`, and with no `AI_API_KEY` it uses the mock AI provider.

## Running

Two terminals:

```bash
# backend — from backend/
uvicorn app.main:app --reload --port 8000

# frontend — from frontend/
npm run dev
```

The UI is at `http://localhost:5173`, the API at `http://127.0.0.1:8000`, and interactive docs at
`http://127.0.0.1:8000/docs`.

**Create a user before signing in.** The UI always presents a login screen, and no account exists by
default:

```bash
# from backend/
python -m scripts.create_user --email you@example.com --role admin
```

The password is prompted for without echoing. See [SECURITY.md](SECURITY.md#setting-up-authentication).

Then click **Run 50-record batch** on the overview page to generate synthetic data and see a
populated dashboard.

## Environment variables

### Core

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./razorz.db` | Connection string; `postgres://` and `postgresql://` are rewritten to `postgresql+psycopg://` |
| `ENVIRONMENT` | `development` | `production` refuses to serve without auth configured |
| `DEBUG` | `true` | Debug flag |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | First CORS allowed origin |

For any host other than `localhost`/`127.0.0.1`, `sslmode=require` and `connect_timeout=10` are added
automatically.

### Authentication

| Variable | Default | Purpose |
|---|---|---|
| `RAZORZ_API_KEYS` | `""` | Comma-separated machine keys — **secret** |
| `RAZORZ_AUTH_REQUIRED` | `false` | Require a credential even with no API keys |
| `RAZORZ_ADMIN_EMAIL` | `""` | Bootstrap admin at startup |
| `RAZORZ_ADMIN_PASSWORD` | `""` | **Secret** |
| `RAZORZ_ADMIN_NAME` | `Administrator` | Display name |

### AI

| Variable | Default | Purpose |
|---|---|---|
| `AI_PROVIDER` | `mock` | `mock`, `gemini`, or `http_llm` |
| `AI_MODEL` | `mock-model` | Model name |
| `AI_API_KEY` | `""` | **Secret** |
| `AI_BASE_URL` | Gemini v1beta endpoint | LLM host |
| `AI_TIMEOUT_SECONDS` | `20` | Request timeout |

### Reconciliation thresholds

| Variable | Default |
|---|---|
| `RECON_AUTO_RESOLVE` | `0.99` |
| `RECON_AUTO_RESOLVE_WARNING` | `0.90` |
| `RECON_HUMAN_REVIEW` | `0.70` |

Changing these changes financial outcomes. They exist for calibration, not tuning against
evaluation data — see [EVALUATION.md](EVALUATION.md#the-known-accuracy-gap).

### Razorpay and frontend

Razorpay variables are documented in [RAZORPAY.md](RAZORPAY.md#configuration). The frontend reads only
`VITE_API_KEY` (optional, from `frontend/.env`).

### Loading order

`backend/app/config.py` loads `<root>/.env` and then `<root>/backend/.env`. Because `load_dotenv` does
not override variables that are already set, **the root `.env` wins** on any key defined in both.

`Settings` is a frozen dataclass evaluated once at import, so **every environment change requires a
restart** — including API key rotation.

## Testing

```bash
# from backend/
pytest -q                              # 292 tests
pytest -q tests/test_user_auth.py      # one file
pytest -q -k webhook                   # by keyword
```

`tests/conftest.py` sets `DATABASE_URL=sqlite://` **before** the app is imported, so the suite cannot
reach a real database even when `.env` points at one. The AI provider defaults to the mock and the
Razorpay client is faked, so no test needs a secret or a network.

Auth tests hash passwords with reduced PBKDF2 rounds for speed, while a separate test asserts the
production constant is at least 600,000.

## Frontend checks

```bash
# from frontend/
npx tsc --noEmit    # typecheck — not run by CI
npm run build
```

`vite build` does **not** typecheck, and there is no `typecheck` script, so run `tsc` manually. This
has caught real errors that a passing build would have hidden.

## Continuous integration

`.github/workflows/ci.yml` runs on push and pull request to `main`, with two parallel jobs:

- **Backend** — Python 3.13, pip cache on `backend/requirements.txt`, `pip install -r
  requirements.txt`, `pytest -q`
- **Frontend** — Node 22, npm cache on `frontend/package-lock.json`, `npm ci`, `npm run build`

Separate jobs mean a frontend break cannot mask a backend break. `permissions: contents: read`
applies least privilege, and a `concurrency` group cancels superseded runs.

No secrets are referenced, and none are needed — the suite is self-contained by construction.

**CI does not typecheck the frontend.** Adding `npx tsc --noEmit` to that job would close the gap.

## Evaluation

```bash
# from backend/
python -m scripts.run_evaluation --cycles 34
python -m scripts.run_comparison --cycles 34
```

See [EVALUATION.md](EVALUATION.md).

## Repository layout and gitignore

`docs/` holds internal authoring prompts and working notes and is **git-ignored**. Public
documentation is this `documentation/` directory plus the root `README.md`, both tracked.

Ignored: `.env` and `.env.*` at any depth (so `backend/.env` is covered) except `.env.example`,
virtualenvs, `__pycache__`, `*.db`, `node_modules/`, `dist/`.

If you add a document intended for readers, put it in `documentation/`.

## Troubleshooting

**Refreshing the browser shows raw JSON.** Five API prefixes are also client-side routes. The dev
proxy handles this by serving `index.html` to `text/html` navigations; a production static host needs
the same SPA-fallback rule. See [FRONTEND.md](FRONTEND.md#the-navigation-versus-fetch-bypass).

**"API returned HTML instead of JSON."** Uvicorn is not running on port 8000, or the route is missing
from the Vite proxy table.

**"Invalid email or password" on the very first login.** No account exists yet — run
`scripts/create_user.py`. The message is identical for an unknown email by design, so it is not
evidence of a wrong password.

**First request after a restart takes 10–25 seconds.** One-time schema bootstrap against remote
Postgres. Warm requests are 1–3 seconds.

**`getaddrinfo failed` on startup.** The direct Supabase host resolves over IPv6 only, so a DNS or
IPv6 blip breaks startup. Retrying usually works; the pooler host on port 6543 is the durable fix.

**A new API route returns an empty response in dev.** Add its prefix to the proxy table in
`vite.config.ts`.

**Dashboard and exceptions counts disagree.** Expected: the dashboard counts the latest run while the
exceptions list spans all runs and all statuses. See
[DATABASE.md](DATABASE.md#the-exception-scoping-gap).

## Known limitations

Would need attention before a real deployment:

- **No role-based authorization** — any signed-in user can resolve or reject exceptions
- **No rate limiting** — `/auth/login` is brute-forcible; `/copilot/ask` and the webhook are unthrottled
- **Session tokens in `sessionStorage`** — XSS-exposed; an httpOnly cookie plus CSRF is stronger
- **No migration tool** — schema is created by `create_all` plus a best-effort `ADD COLUMN` pass;
  renames, type changes and drops are unsupported
- **Exception counts are not run-scoped** — the `exceptions` table has no `run_id`
- **The polling Razorpay sync writes no audit event**, and a large run's per-decision rows crowd the
  first page of the audit trail
- **CI does not typecheck the frontend**
- **Deployment configuration does not exist yet** — no Dockerfile, no host config
- **`AMBIGUOUS_MATCH` vs `PAYMENT_MISSING` classification gap** at scale, left unfixed on purpose to
  avoid tuning against held-out data
- **Test Mode settlements** are often empty, so settlement coverage against Razorpay sandbox data is
  naturally sparse
