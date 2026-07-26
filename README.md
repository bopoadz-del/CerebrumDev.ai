# CerebrumDev.ai — the Factory

**One account. Tell the factory. Receive your platform.**

CerebrumDev.ai is a conversational platform factory. A user registers, lands on
the **Factory Floor**, describes the platform they need in plain language, and
the factory drafts a blueprint. On the user's word — *approve* — the generator
builds a brand-new, exportable platform from certified Cerebrum-Blocks kernels.
The subscription plan decides how deep the factory builds.

Live deployment (Render):

- Frontend (Factory Floor): `https://cerebrumdev-frontend.onrender.com`
- Backend API: `https://cerebrumdev-backend.onrender.com`
- Block store: `https://cerebrum-blocks.onrender.com`

## The flow (what a user experiences)

1. **Register / sign in** — email + password. Email verification is built in
   (SMTP-configured deployments send mail; unconfigured ones surface a dev
   token honestly in the UI). Password reset is self-serve.
2. **Factory Floor chat** — "Build me a multi-user platform for …". The
   product architect drafts a blueprint (golden steward for estate briefs,
   LLM drafting when enabled, deterministic keyword drafting always available
   offline).
3. **Approve & build** — the capability planner resolves every capability
   against the dual registry (fail-closed: unknown blocks → UNSUPPORTED), then
   the generator writes the platform.
4. **Your Platforms** — download the export zip: a real, runnable platform
   tree (FastAPI app, vendored certified blocks, resident-engineer charter,
   Dockerfile, render.yaml, README, factory plan).
5. **Subscription** — every account starts on a free trial (default 3 days,
   full access). Upgrade is Stripe checkout; the portal manages the
   subscription. On deployments where the owner has not linked Stripe, the UI
   says so plainly — that is the single remaining owner-side configuration.

## Multi-user by construction

Every session is owned by an account. A shared `require_owned_session` guard
backs **all** session-scoped routers (chat, config, upload, train, deploy,
drive, session_product): user credentials (`cdt_` login tokens, `cdk_` API
keys) only ever reach their own sessions — cross-account access gets a
non-leaking 404. Admin/master and local-dev principals pass through.
Regression-covered in `backend/app/tests/test_session_ownership.py`.

## Observability (Sentry)

Error tracking and performance are wired on both tiers and activate by DSN:

- Backend: set `SENTRY_DSN` (Sentry project platform *python/fastapi*).
  Initialization lives in `app/core/observability.py`, runs at package import,
  and is inert without a DSN. Release is tagged from `RENDER_GIT_COMMIT`.
- Frontend: set `VITE_SENTRY_DSN` (platform *javascript-react*) on the static
  site. The bundle loads Sentry lazily — zero cost when unset.

## Platform status

- **Auth**: register, login, email verification, password reset, per-account
  API keys — live. Rate-limited (Redis-backed when `REDIS_URL` is set,
  in-memory otherwise).
- **Accounts DB**: sqlite by default, Postgres via `ACCOUNTS_DATABASE_URL`.
  Migrations run through **Alembic** (`backend/alembic/`); the container
  applies `alembic upgrade head` at boot.
- **Chat → LLM → blueprint → plan → generate → export**: live E2E.
- **Billing**: trial lifecycle, entitlement checks, Stripe checkout/portal/
  webhook are implemented. Stripe keys are the owner's dashboard
  configuration (`STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`,
  `STRIPE_WEBHOOK_SECRET`) — without them the API answers
  `503 stripe_not_configured` honestly. Enforcement flips via
  `BILLING_ENFORCEMENT=true`.
- **Drive connector**: OAuth pending states are Redis-backed when `REDIS_URL`
  is set.

## Under the floor (how platforms are built)

- **Dual registry + capability planner** — capabilities resolve to REUSE /
  ADAPT / COMPOSE / GENERATE / STUB; anything unknown fails closed.
- **Certified kernels** come from the Cerebrum-Blocks store (the 24-kit
  universal kernel); the factory never ships unregistered blocks.
- **Resident Engineer** — each generated platform carries its resident
  engineer charter (observe/diagnose/repair under human authority).
- **Reasoning layer** — chain generation, rule injection.

## Development

```bash
# backend
cd backend && pip install -r requirements.txt -r requirements-dev.txt
python -m pytest

# frontend
cd frontend && npm install && npm run build && npm run lint && npm run test

# accounts migrations
cd backend && alembic upgrade head
```

## Repository layout

- `backend/app/routers/` — API: accounts, sessions, chat (SSE), product,
  billing, deploy, drive, domains/RAG, resident engineer, workbench
- `backend/app/factory/` — product architect, planner, dual registry,
  generator, platform chat flow
- `backend/app/core/` — auth, accounts store, billing, rate limit,
  observability, session guard
- `backend/alembic/` — accounts DB migrations
- `frontend/src/` — the Factory Floor console (React + Vite)
- `docs/reports/` — readiness audits and generation proofs
