# RAZORZ Documentation

Technical documentation for RAZORZ, an AI Finance Controller for multi-source financial
reconciliation. For the project pitch and feature overview, see the
[root README](../README.md).

## Start here

| If you want to… | Read |
|---|---|
| Understand how the pieces fit together | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Run the project locally | [DEVELOPMENT.md](DEVELOPMENT.md) |
| Know how matching decisions are made | [RECONCILIATION.md](RECONCILIATION.md) |
| Call the API | [API.md](API.md) |
| Understand the schema | [DATABASE.md](DATABASE.md) |
| Know what the AI is allowed to do | [AI_AND_COPILOT.md](AI_AND_COPILOT.md) |
| Set up authentication | [SECURITY.md](SECURITY.md) |
| Connect Razorpay | [RAZORPAY.md](RAZORPAY.md) |
| See measured accuracy | [EVALUATION.md](EVALUATION.md) |
| Work on the UI | [FRONTEND.md](FRONTEND.md) |

## The one idea that explains the rest

**Deterministic arithmetic decides; AI only explains.**

Every financial decision — whether two records match, by how much they differ, what status a
relationship gets — is computed by explicit rules over `Decimal` values in
`backend/app/reconciliation/`. The AI layer receives the *result* of those rules and produces
narrative: a likely cause, investigation steps, a suggested reviewer note. Its output schema
contains no status, amount, or counterpart field, so it is structurally incapable of changing a
number. A runtime guard re-checks this, and the
[evaluation harness](EVALUATION.md) measures it: enabling AI moves no accuracy metric.

This is a deliberate trade. It costs the system any chance of AI-driven matching gains, and buys
an audit trail where every rupee is traceable to a rule rather than a model.

## Conventions used throughout these docs

- **Money is `Decimal`, never `float`**, and crosses the API boundary as a **string**
  (`"1234.50"`, not `1234.5`) so no client can silently reintroduce float rounding.
- **Two separate status vocabularies.** Reconciliation relationship statuses are uppercase
  (`MATCHED`, `REVIEW_REQUIRED`); source-record statuses mirror the provider's own lowercase
  values (`captured`, `pending`). They are unrelated namespaces.
- **A "record" in the ledger UI is one relationship**, not one transaction. An order paid by one
  payment produces one `order_payment` row.
- Code references are given as `path:line` against the repository root.

## Honesty about scope

RAZORZ is a working demonstration, not a production finance system. Documented limitations are
kept alongside the features they qualify rather than in a separate list, and
[DEVELOPMENT.md](DEVELOPMENT.md#known-limitations) collects the ones that would block a real
deployment. Where behaviour is odd but deliberate, the docs say so; where it is odd and probably
a bug, they say that too.
