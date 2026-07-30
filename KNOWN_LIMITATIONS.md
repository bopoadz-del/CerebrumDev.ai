# Known Limitations — CerebrumDev.ai

Leading with the worst honest number, per house standard.

## The worst number first

**Without an LLM key, every generated blueprint is generic.** The
deterministic architect fallback names the product "Product Platform",
vertical "product", regardless of what the user asked for — verified live in
the 2026-07-29 stranger walk (a request for a retail store platform produced
exactly that). The pipeline works end to end (register → chat → blueprint →
approve → generate → 63 KB zip in 4 API calls), but the quality of the
drafted blueprint depends entirely on a configured LLM. A related number
from the store side applies to every generated product's retrieval:
corpus-blind questions hit a relevant KB entry at 0.25 hit@5
(Cerebrum-Blocks `KNOWN_LIMITATIONS.md`).

## Generated products are prototypes

- Output is a working scaffold — real code, tests, Dockerfile, render.yaml,
  product DNA — not a finished production system.
- Third-party connectors in generated products are stubs until the owner
  supplies credentials.
- Deployment is a step the owner runs; nothing deploys automatically.

## Accounts, email, and state

- Without SMTP configured, registration and password reset return their
  verification/reset tokens **in the API response** (labeled `dev_token`
  mode). Honest, but on a public deployment it means unverified accounts
  proceed and tokens are visible to the caller.
- Sessions are JSON files on disk and per-account trial counters live in the
  accounts DB; with the default sqlite backend both are single-instance.
  Use `ACCOUNTS_DATABASE_URL` (Postgres) and Redis for multi-instance.
- Billing enforcement is opt-in (`BILLING_ENFORCEMENT`); until Stripe env
  keys are set, checkout returns `503 stripe_not_configured` (honest, but
  no revenue path).

## Grounding coverage

- The mandatory grounding stage covers chat replies and the steward
  runtime's retrieval responses. It is lexical: invented URLs and uncited
  figures are caught; fluent prose that is wrong without a checkable claim
  is not.
- Blocked answers are withheld with a generic refusal; the full reason lives
  in the audit log, not the user-visible message.

## Workbench

- The Kimi workbench requires an external CLI; `/health` reports evaluated
  capability (`kimi_workbench_enabled` is flag AND a CLI that answers).
  No deployment today ships the CLI, so the honest value is `false`.
- `KIMI_MOCK` is a mock and is reported as `llm_mock`, never as a
  configured LLM.

## Observability

- Sentry initializes only when `SENTRY_DSN` is set; there are no metrics or
  alerting, and 26 files still log via `print()`.

## Test suite

Two failures and six errors are known and pre-date the free-trial
hardening, reproduced on a clean tree: `test_estate_kit_phase0` (steward
demo emission), `test_harvest_nhtsa` (external script), and the six
`test_steward_pilot_auth` errors (environment-dependent DB setup).
Everything else is green: 647 passed at the time of writing.
