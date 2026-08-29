# Security

Covers the authentication model, what is deliberately not protected, and the known weaknesses. All
credentials come from environment variables; nothing sensitive is committed.

## Two credential types, one dependency

`require_api_key` (`backend/app/security.py:106`) is installed application-wide:

```python
app = FastAPI(title="RAZORZ API", version="0.8.0", dependencies=[Depends(require_api_key)])
```

Installing it on the app rather than per-route means **a newly added endpoint is protected by
default**. Forgetting a decorator cannot silently expose data; you have to opt out explicitly.

It accepts either:

1. **A user session token** — `Authorization: Bearer <token>` from `POST /auth/login`. Opaque,
   server-side, revocable.
2. **A machine API key** — `X-API-Key: <key>`, or `Authorization: Bearer <key>` as a fallback, drawn
   from the comma-separated `RAZORZ_API_KEYS`.

Resolution order: preflight and public paths pass through; then a session token; then an API key;
then the enforcement decision below.

## Public paths

```
/health
/docs, /docs/oauth2-redirect, /redoc, /openapi.json
/auth/login
/integrations/razorpay/webhook
```

`/auth/login` cannot require a credential — it exists to issue one. The webhook is exempt because it
authenticates by HMAC signature over the raw body; layering API-key auth on top would mean giving
Razorpay a second secret for no additional security. `OPTIONS` is always allowed, since CORS
preflight carries no credentials by design.

Trailing slashes are normalized, so `/health/` also matches.

## When authentication is enforced

`auth_enabled()` is true when `RAZORZ_API_KEYS` is non-empty **or** `RAZORZ_AUTH_REQUIRED=true`.

| Situation | Behaviour |
|---|---|
| Keys or the flag configured | Protected routes require a credential; missing/invalid → `401` |
| Neither configured, non-production | **Protected routes are open** |
| Neither configured, `ENVIRONMENT=production` | Every protected route returns `503 auth_not_configured` |

The open-in-development default keeps the local loop and the 292-test suite working without
ceremony. Production refuses to serve rather than serving unauthenticated — a misconfigured deploy
fails loudly instead of leaking quietly.

**The UI always shows a login screen regardless**, because the frontend guard is client-side and
independent of backend enforcement. Seeing the login form is therefore *not* evidence that the API is
protected. Check `GET /health` → `auth.required`.

## Passwords

`backend/app/auth/passwords.py`, standard library only — no new dependency:

- **PBKDF2-HMAC-SHA256, 600,000 iterations**, 16-byte random salt per password, 32-byte output
- Encoded as `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`
- Verified with `hmac.compare_digest`; malformed or empty hashes fail closed
- Minimum length 10, maximum 200
- `needs_rehash()` flags hashes using a weaker algorithm or fewer iterations

Argon2id would be a stronger choice, but PBKDF2 at 600k iterations meets current OWASP guidance for
PBKDF2-SHA256 and requires no third-party package. The tests hash with far fewer rounds for speed,
while one test asserts the production constant is at least 600,000 — so the fast path cannot silently
become the real one.

## Sessions

Opaque `secrets.token_urlsafe(32)` tokens. **Only a SHA-256 hash of the token is stored**, so a
database leak yields nothing usable. Twelve-hour expiry; logout sets `revoked_at`.

Server-side sessions were chosen over JWT deliberately. Logout becomes real revocation rather than the
client agreeing to forget a still-valid token, there is no signing secret to rotate or leak, and
deactivating a user invalidates their live sessions immediately. The cost is a database lookup per
request, which is negligible here.

`resolve_session` rejects unknown, revoked and expired tokens, and tokens belonging to a deactivated
user.

## Account enumeration and timing

`POST /auth/login` returns the identical message and code for an unknown email and a wrong password.
On an unknown email, `authenticate` still runs a verification against a dummy hash so the response
time is comparable, preventing enumeration by timing.

## What is never logged

Passwords, tokens, token hashes, session ids and API keys are never written to logs, audit rows or
responses. The only rejection log line is `Rejected unauthenticated request to <path>` — path only.

`auth_event` audit rows record the account and the outcome (`login`, `login_failed`, `logout`,
`user_created`) and nothing else. `webhook_event` rows store no signature, headers or body — only a
digest. `ai_assistance` and `copilot_query` rows store no prompt text.

Tests assert the absence of the attempted password, the session token and its hash from both audit
rows and captured logs.

## Webhook authentication

HMAC-SHA256 over the **raw request bytes**, compared with `hmac.compare_digest` against
`X-Razorpay-Signature`. Verifying the raw body rather than a re-serialized payload matters: any
JSON round-trip could reorder keys and break the signature.

With no `RAZORPAY_WEBHOOK_SECRET` configured the endpoint returns `503` rather than accepting
unverified deliveries. Invalid signature → `401`, audited, nothing ingested.

## Database access control

On Postgres, `lock_public_tables` enables row-level security on all 13 tables and revokes privileges
from the Supabase Data API roles `anon` and `authenticated`. The application connects as the table
owner and bypasses RLS; the public REST endpoint gets nothing.

**RLS is enabled with no policies**, which is the intent for a single-tenant owner-connection app —
but it means any non-owner connection sees zero rows, and adding a second role later requires
explicit policies.

## Setting up authentication

1. Create a user, from `backend/`:

```bash
python -m scripts.create_user --email you@example.com --role admin
```

The password is read from a hidden prompt (or `RAZORZ_NEW_PASSWORD`) and never appears in argv.
`--reset-password` updates an existing account.

2. Optionally bootstrap the first admin at startup with `RAZORZ_ADMIN_EMAIL`,
   `RAZORZ_ADMIN_PASSWORD` and `RAZORZ_ADMIN_NAME`. The account is created only if that email does
   not already exist, and the password is never logged.
3. To enforce API-level auth locally, set `RAZORZ_AUTH_REQUIRED=true` and restart.

Roles are `admin`, `reviewer` and `viewer`.

## Known weaknesses

These are real and unfixed. Listed so nobody mistakes this for a hardened system.

**No role-based authorization.** `role` is stored and displayed but never checked. Any signed-in
user, including a `viewer`, can resolve or reject exceptions.

**No rate limiting anywhere.** `POST /auth/login` accepts unlimited attempts, so passwords are
brute-forcible at network speed. `/copilot/ask` and the webhook are likewise unthrottled.

**Session tokens live in `sessionStorage`**, readable by any injected script — the same XSS exposure
as the API key it replaced. An httpOnly cookie with CSRF protection is the stronger design.

**API key rotation requires a restart.** `configured_keys()` re-reads `settings.razorz_api_keys` per
request, but `Settings` is a frozen dataclass fixed at import, so the value cannot change in a live
process. The docstring claiming otherwise is wrong.

**No password reset or user management UI.** Accounts are created only via the CLI script or the
startup bootstrap.

**A browser-held API key is not a secret.** `VITE_API_KEY` is compiled into the bundle. It exists for
convenience against a demo backend; session login is the real mechanism.

**No account lockout, no password history, no MFA.**
