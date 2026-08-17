# CerebrumDev.ai — production-readiness audit

**Repository:** [bopoadz-del/CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai) (the Factory). Not Cerebrum-Steward.  
**Auditor window (UTC):** 2026-08-17T00:24Z–00:33Z  
**Prior pin:** 2026-08-16T23:48Z–23:51Z (NO-GO; accounts still Render Postgres).  
**Rule:** PASS / FAIL / NOT VERIFIED only. No readiness %. Deterministic or fallback success is not production evidence. Live evidence required.

| Pin | Value |
| --- | --- |
| Live Render backend commit | `71593c2bcc09e0882656d1ea2617e686a9084cc9` (`dep-da14vsm7bikc738c8brg`, status `live`, finished 2026-08-17T00:04:36Z) |
| Live `/version` | `git_sha=71593c2bcc09e0882656d1ea2617e686a9084cc9` |
| Live Render frontend commit | `bffeeaeb06fa0dc6f2cb3e0b66387bf70c2a5867` (`dep-da14i5ur33ss73f97afg`, finished 2026-08-16T23:35:12Z) — behind backend |
| `origin/master` at audit | `71593c2bcc09e0882656d1ea2617e686a9084cc9` |

---

## Verdict

| Claim | Result |
| --- | --- |
| **Production** | **NO-GO** |
| **Unattended public demo** | **NO-GO** |
| **Owner-supervised walkthrough** | Factory loop is `[LIVE]` on `https://api.cerebrum-dev.com` with `SMOKE_GATE_TOKEN`. Accounts engine is **Neon**. Redis and Sentry stay live. **Not** a production claim: Stripe is unset, `/ready` `last_backup` is still pre-cutover, and Render Postgres prior rows could not be verified. |

The 00:17–00:19Z smoke DEAD (`session create`) was a **missing local gate token**, not a broken session API. Unauth `POST /v1/auth/smoke-login` is 401 (endpoint exists). With the Render secret loaded locally, smoke exit 0.

---

## Domain scores

| # | Domain | Status | Live evidence |
| --- | --- | --- | --- |
| 1 | Deploy pin (`origin/master` vs live Render SHA) | **PASS** (backend) / **FAIL** (frontend lag) | Backend live commit equals `origin/master` `71593c2`. Frontend live is still `bffeeae`. |
| 2 | Live smoke / kernels `[LIVE]` | **PASS** | See [Mandatory live gate](#mandatory-live-gate). Smoke exit 0 at 2026-08-17T00:32:48Z. Every kernel `[LIVE]`, including Redis and LLM drafting (`architect_llm`, caps=6 populated=6). Billing checkout 503 `stripe_not_configured` is the honest PASS. |
| 3 | Readiness & ops | **FAIL** | `/ready` HTTP 200, `HEAD /ready` HTTP 200, `/version` HTTP 200. Uvicorn binds `0.0.0.0:$PORT`. Plan **starter**; disk `cerebrumdev-storage` 1GB `/app/storage`. **`last_backup.at` is still 2026-08-16T23:49:44Z** (pre-Neon cutover). This PR records `accounts_host` on each backup and re-snapshots when the live host changes. |
| 4 | Auth / security | **PASS** | Unauth writes 401. Public register + unverified session → **403** `email_not_verified`. Smoke uses `SMOKE_GATE_TOKEN` / `/v1/auth/smoke-login`; public verification stays on. |
| 5 | CI on latest master | **PASS** | SHA `71593c2` is merged `origin/master`. This branch adds tests; required CI is the merge gate. |
| 6 | Data / migrations | **FAIL** | Live `ACCOUNTS_DATABASE_URL` host class is **`*.aws.neon.tech`**, project `cerebrumdev` / `shy-glade-57354706`, db `cerebrumdev-accounts`, alembic **0002**. Boot at 00:04Z ran empty `- → 0001 → 0002` (no row copy). Neon now holds post-cutover canary/smoke rows only. Render Postgres `dpg-d9vlh6c9v7es73b53l9g-a` still exists (free, expires 2026-09-13) but **external SSL connections fail** (MCP + local `psycopg`, 4 retries). **Not dropped.** Neon PITR branch `backup-post-cutover-20260817` (`br-cool-voice-ayosx4yy`) taken 2026-08-17T00:29Z. Fail-closed: prior Render accounts could have been lost. |
| 7 | Factory vs product confusion | **PASS** | No runnable `backend/app/retailops/` tree. RetailOps remains provenance docs + sibling product repo. |
| 8 | Frontend production | **PASS** | `https://cerebrum-dev.com` and `https://www.cerebrum-dev.com` HTTP 200. `VITE_API_URL` is `https://api.cerebrum-dev.com`. Live SHA is behind backend (docs-only `71593c2` did not rebuild the static site). |
| 9 | Observability / safe errors | **PASS** | `/health` `sentry.configured: true`. `/version` `sentry_configured: true`. Redis `configured: true`, `ok: true`. Do not regress. |
| 10 | Docs vs reality | **PASS** | This audit matches live evidence: Neon accounts engine, smoke `[LIVE]`, Stripe unset, stale `last_backup`, Render Postgres still present and unreachable. |

Machine-readable copy: [`artifacts/cerebrumdev_production_readiness.json`](../../artifacts/cerebrumdev_production_readiness.json).

---

## Mandatory live gate (AGENTS.md)

```bash
python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com
```

Requires `SMOKE_GATE_TOKEN` (Render secret) or a verified `SMOKE_EMAIL`/`SMOKE_PASSWORD`. Public email verification stays fail-closed. Put the same secret in the **Cloud Agents dashboard** (account-wide); do not commit it. Smoke retries transient 502/504 on GET/chat (observed once immediately after an LLM draft).

| Target | Timestamp (UTC) | Result |
| --- | --- | --- |
| `python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com` | 2026-08-17T00:18:51Z | **SMOKE FAIL** (exit 1) — no local `SMOKE_GATE_TOKEN`; `session create` DEAD |
| same, with Render `SMOKE_GATE_TOKEN` | 2026-08-17T00:32:48Z | **SMOKE PASS** (exit 0) |

Smoke transcript against the live custom-domain API (no tokens logged):

```
post-deploy smoke against https://api.cerebrum-dev.com
[LIVE] health endpoint — status=ok
[LIVE] redis rate limiting configured — redis={'configured': True, 'ok': True}
[LIVE] version endpoint — http=200 sha=71593c2bcc09
[LIVE] auth register — http=201
[LIVE] unverified session denied — http=403
[LIVE] session create — http=200
[LIVE] chat blueprint event — sse_bytes=5643
[LIVE] LLM drafting (not fallback) — http=200 mode=architect_llm caps=6 populated=6 (fallback fingerprint: caps=2 populated=0)
[LIVE] approve -> generation event
[LIVE] generation recorded — product_id=winery-management
[LIVE] export zip — http=200 files=181
[LIVE] grounding (no invented deploy/URL)
[LIVE] cross-account isolation — http=404 (expect 404)
[LIVE] billing status structured — http=200
[LIVE] billing checkout honest — http=503 (503 stripe_not_configured or real url)
SMOKE PASS: every kernel live.
```

LLM / generate / grounding / isolation / billing / Redis kernels are **LIVE**. Deterministic fallback was not accepted (`architect_llm`, populated capabilities 6, not the fallback fingerprint).

---

## Infra follow-ups from this window

Render MCP workspace **My Workspace** `tea-d9rteq2jnfac738dnc70`:

| Resource | Id | Plan / note |
| --- | --- | --- |
| Web `cerebrumdev-backend` | `srv-d9ta2pad0e5s738lllpg` | **starter**, slug `cerebrumdev-backend-goia`, `numInstances: 1` |
| Disk `cerebrumdev-storage` | `dsk-da14odgu01pc739fkrvg` | 1GB, mount `/app/storage` |
| Static `cerebrumdev-frontend` | `srv-d9ta36v40ujc73dsmhkg` | slug `cerebrumdev-frontend-kkz2`, live SHA `bffeeae` |
| Postgres `cerebrumdev-accounts` | `dpg-d9vlh6c9v7es73b53l9g-a` | **free**, expires 2026-09-13. **Still present. Not the live accounts URL. Not dropped.** External SSL connect **FAIL**. |
| Key Value `cerebrumdev-redis` | `red-da131ae1egvs739s8ihg` | **starter**, Oregon, `available`. `/health` redis ok. |

Neon (Factory accounts, not The Fork):

| | |
| --- | --- |
| Project | `cerebrumdev` / `shy-glade-57354706` |
| Org | CHADi (`org-noisy-mud-31415347`) |
| Branch | `main` (`br-round-frog-ayvfw6nu`) |
| PITR copy | `backup-post-cutover-20260817` (`br-cool-voice-ayosx4yy`) |
| Database | `cerebrumdev-accounts` |
| Alembic | `0002` |
| Host class | `*.aws.neon.tech` |

The Fork (`the-fork` / `round-shadow-39311244`) was not reused.

---

## Top blockers (must fix before any production claim)

1. **`/ready` `last_backup` is pre-cutover** (2026-08-16T23:49:44Z). This PR re-snapshots when `ACCOUNTS_DATABASE_URL` host changes. Re-probe `/ready` after deploy; do not claim GO on the old timestamp.
2. **Prior Render Postgres rows were not migrated.** Empty Neon alembic at cutover; source PG SSL connections fail. Do not drop `dpg-d9vlh6c9v7es73b53l9g-a` until a successful dump/count exists.
3. **Stripe billing is honestly unconfigured.** No `STRIPE_*` keys on the backend. Checkout 503 `stripe_not_configured` is correct. Do not invent secrets.

Do not “fix” this by weakening CI, by pointing Factory at The Fork’s Neon project, or by accepting deterministic architect fallback as `[LIVE]`.

---

## What landed in this window (not a GO)

- Live smoke exit 0 on SHA `71593c2` with a local `SMOKE_GATE_TOKEN` pulled from Render env (not printed, not committed).
- Factory accounts URL classifies as Neon; Redis and Sentry still PASS.
- Neon backup branch taken; Render Postgres left in place.
- Smoke client retries transient 502/504; backup scheduler records accounts host and re-runs after a cutover.

---

## What is already solid (not a GO)

- Backend live SHA equals `origin/master` (`71593c2`).
- Custom domains answer: UI `https://cerebrum-dev.com`, API `https://api.cerebrum-dev.com`.
- Auth fail-closed on protected routes; email verification enforced for public users.
- Factory LLM loop `[LIVE]` (draft → approve → generate → zip → grounding → isolation).
- Redis rate limiting `[LIVE]`.
- Sentry configured on backend `/health` and `/version`.
- No RetailOps runtime in this repo.
