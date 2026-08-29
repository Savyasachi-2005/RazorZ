# Frontend

React 19 + TypeScript 5.9 + Vite 7 + Tailwind CSS 3.4. Dark, dense, keyboard-friendly — built to read
like a finance console rather than a marketing dashboard.

## Pages

| Route | Component | What the user does |
|---|---|---|
| *(unauthenticated)* | `LoginPage` | Signs in with email and password |
| `/` | `OverviewPage` | Reads KPI cards, match-rate health bar and recent activity; runs a 50-record batch |
| `/reconciliation` | `ReconciliationPage` | Filters the ledger by status, relationship type and id search |
| `/exceptions` | `ExceptionsPage` | Filters the exception queue by status, type and severity; supports `?record=` deep links |
| `/exceptions/:exceptionId` | `ExceptionDetailPage` | Investigates one break, requests AI assistance, resolves or rejects with a note |
| `/copilot` | `CopilotPage` | Asks read-only questions; the thread persists in `sessionStorage` |
| `/audit` | `AuditPage` | Browses the trail with actor/event/action filters, table or timeline view |
| `/sources` | `SourcesPage` | Checks generator and Razorpay status; runs a batch or a sync |
| `*` | — | Redirects to `/` |

Every ledger row is **one relationship**, not one transaction — a `MATCHED` row showing a difference
of ₹0.00 means the two sides agree.

## State architecture

Provider nesting in `main.tsx`: `BrowserRouter` → `ToastProvider` → `AuthProvider` → `App`.

**`AuthProvider`** owns `user`, `checking`, `signingIn`, `signingOut` and `error`. On mount it reads
the token from `sessionStorage`; with no token it issues **no request at all** and goes straight to the
login screen. With a token it calls `api.me()` and clears the session if that fails. It also registers
a global handler so **any 401 from any request** clears the session and returns to login with "Your
session has expired. Please sign in again."

**`AppDataProvider`** owns `summary`, `records`, `exceptions`, `audit`, `healthStatus`, `notice`,
`error`, `initialLoading`, `refreshing`, `busy`, `busyLabel` and `reviewNotes`. It refreshes five keys
— `summary`, `records`, `exceptions`, `audit`, `health` — and coalesces concurrent full refreshes
through a single in-flight promise. A module-level `workspaceBooted` flag prevents a double initial
load under React StrictMode.

**`ToastProvider`** owns the notification queue: tones `success | error | info | warning`, 4200 ms
default duration, 6500 ms for errors, at most four queued.

### The guard controls fetching, not just rendering

`AppDataProvider` is mounted **inside** the authenticated branch of `App`, so no dashboard request is
issued before sign-in. Guarding only the rendering would still have fired every fetch on mount and
produced a screenful of 401s behind the login form.

`App` shows "Restoring session…" while `checking`, then either `LoginPage` or the routed shell.

Two behaviours worth knowing: `review()` does not trigger a full reload — it patches the exception in
place and optimistically prepends an audit row, so the trail updates without a round trip.
`getException(id)` returns a cached row when available and only fetches otherwise.

## `api.ts`

One `request<T>()` chokepoint wraps every call, using relative paths and relying on the Vite proxy in
dev.

**Headers** — `Content-Type: application/json`, plus `Authorization: Bearer <token>` when a session
exists, plus `X-API-Key` only when `VITE_API_KEY` is set **and** no session token is present. A
session always takes precedence over the build-time key.

**Session storage** — key `razorz.session.token` in `sessionStorage`, so it clears when the tab
closes. All access is wrapped in try/catch for environments where storage is unavailable.

**401 handling** — any 401 outside `/auth/login` clears the session, invokes the global handler and
throws `UnauthorizedError`.

**Error messages are diagnostic on purpose.** The helper detects an HTML body and says "API returned
HTML instead of JSON — is the backend running on http://127.0.0.1:8000?"; an empty body names uvicorn
and `vite.config.ts` as the things to check. These are the two failure modes that actually happen in
dev, and a generic "request failed" would send you hunting.

**Methods** — `login`, `logout`, `me`, `health`, `summary`, `records`, `exceptions`, `exception`,
`audit`, `generate`, `review`, `aiAssist`, `copilotSuggestions`, `copilotAsk`, `razorpayStatus`,
`razorpaySync`.

Hardcoded parameters: `records()` requests `limit=200`; `exceptions()` 50; `audit()` 100;
`generate()` posts `seed: 42`; `review()` posts `actor: "finance-ops"`; `copilotAsk` sends only the
last four turns; `razorpaySync` defaults to 50.

## Design system

Tailwind tokens, all under `theme.extend` in `tailwind.config.js`:

| Token | Values |
|---|---|
| `canvas` | `#070b14` |
| `surface` | `#0d1424`, `raised #121a2c`, `hover #182236` |
| `line` | `#1e2a3f`, `strong #2a3a55` |
| `ink` | `#f1f5f9`, `muted #94a3b8`, `faint #64748b` |
| `accent` | `#16a34a`, `soft #14532d`, `text #4ade80` |
| `warn` | `#d97706`, `soft #78350f`, `text #fbbf24` |
| `danger` | `#e11d48`, `soft #881337`, `text #fb7185` |
| `info` | `#2563eb`, `soft #1e3a5f`, `text #93c5fd` |

Fonts: IBM Plex Sans and IBM Plex Mono. Also `maxWidth.shell: 1480px`,
`transitionDuration.fast: 150ms`, and `boxShadow.panel`. There is no custom spacing scale — sizes such
as `w-[240px]` are arbitrary values.

Green means reconciled, amber means needs a human, rose means a break. Monospace is reserved for ids
and amounts so columns align for scanning.

`index.css` adds `color-scheme: dark`, CSS variables mirroring the palette, a radial-gradient body
background, custom scrollbars, and the `.focus-ring` and `.tabular-nums` component classes.

**Components** (`src/components/ui/`): `Button` (`primary | secondary | danger | ghost`, with
`loading`), `Badges` (`StatusBadge`, `SeverityBadge`, `ConfidenceBadge`), `EmptyState` (plus
`ErrorBanner`, `Skeleton`, `PageSkeleton`), `KpiCard`/`SectionCard`, `LoadingFeedback`
(`SyncBanner`, `InlineSpinner`, `LoadingOverlay`), `PageHeader`, `HealthBar`, `GlobalBusyIndicator`,
`Toast`, `Icons`.

`ConfidenceBadge` colours at `>= 99` good, `>= 70` warn, below bad — mirroring the engine's
thresholds. `GlobalBusyIndicator` waits 280 ms before appearing so fast requests don't flash a
spinner.

Formatting helpers in `src/lib/format.ts`: `money` uses
`Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" })`, plus `pct`, `conf`, `labelize`,
`splitPair`, `pairTypeLabel`.

## Layout

`AppShell` renders a **fixed** 240px sidebar at every breakpoint, with the content column offset by
`lg:pl-[240px]`. The nav area scrolls internally while the header, account card and system-status
block stay put.

Fixed positioning rather than `sticky` is deliberate: `sticky` failed to hold here because the
sidebar's own `backdrop-blur` and transform interfered with resolving the scroll ancestor, which let
the account card and sign-out button drift below the fold. Below `lg` the same element becomes a
slide-in drawer with a backdrop.

Sign-out appears twice — a full-width button in the sidebar account card and an icon button in the
mobile top bar — and both disable while the revoke request is in flight.

## Vite configuration

Dev server on port **5173**, proxying to `http://127.0.0.1:8000`: `/auth`, `/health`,
`/reconciliation`, `/exceptions`, `/audit`, `/copilot`, `/ingestion`, `/integrations`.

### The navigation-versus-fetch bypass

Five API prefixes — `/exceptions`, `/audit`, `/reconciliation`, `/copilot`, `/sources` — are **also
client-side routes**. Without special handling, refreshing the browser on `/exceptions` sends the
navigation to the backend and renders raw JSON instead of the app.

The proxy discriminates by `Accept` header: a browser navigation asks for `text/html` and is served
`index.html`, while the app's own `fetch()` calls (which do not) are proxied to the backend.

```ts
bypass: (req) => (req.headers.accept ?? "").includes("text/html") ? "/index.html" : undefined
```

**A production static host needs the equivalent SPA-fallback rule**, or hard refreshes on those five
paths will break in exactly the same way.

**Any new API route must be added to the proxy table**, or the dev UI receives an empty response.

## Build and tooling

```bash
npm ci
npm run build     # vite build
npm run dev       # vite
npm run preview
```

Dependencies: `react ^19.1.1`, `react-dom ^19.1.1`, `react-router-dom ^7.8.2`. Dev: `typescript
^5.9.2`, `vite ^7.1.3`, `@vitejs/plugin-react ^5.0.2`, `tailwindcss ^3.4.17`, `postcss ^8.5.6`,
`autoprefixer ^10.4.21`, plus React type packages.

**There is no `lint`, `test` or `typecheck` script, and `vite build` does not typecheck.** Run
`npx tsc --noEmit` manually before pushing; CI currently runs only the build, so a type error will
not fail it.

The only frontend environment variable is `VITE_API_KEY`, which is optional and compiled into the
bundle — see the caveat in [SECURITY.md](SECURITY.md#known-weaknesses).
