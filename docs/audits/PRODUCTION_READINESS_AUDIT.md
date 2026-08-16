# CerebrumDev.ai — Production-readiness audit

**Repository:** [bopoadz-del/CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai)
**Scope:** Factory (this repo), not Cerebrum-Steward
**Audit time (UTC):** 2026-08-16T21:40Z
**Git pin:** `442b62f5d9a14b9247722320aadbd21ff6be0638` (`origin/master`, live Render commit)

Rule (AGENTS.md): no market-ready / production claim without live post-deploy smoke. Deterministic or fallback success is not evidence.

---

## Verdict

| Claim | Result |
| --- | --- |
| **Production** | **NO-GO** |
| **Unattended public demo** | **NO-GO** |
| **Owner-supervised walkthrough on custom domain** | Possible after email verify; **not** a production claim |

The live SHA matches `origin/master`. CI on `master` is green. Custom domains respond. That is not enough: the documented deploy-gate URL is dead, the smoke script fails on the live API, Redis is unwired, the backend is on Render **free** (spin-down), and the accounts Postgres is a **free** instance that expires **2026-09-13**.

---

## Domain table

| Domain | Status | Evidence |
| --- | --- | --- |
| 1. Identity & deploy pin | **PASS** | Live backend deploy `dep-d9vmms6417fc73a2qiig` commit `442b62f` = `origin/master`. Frontend deploy same SHA. Auto-deploy on `master`. |
| 2. Live smoke / kernels | **FAIL** | `python scripts/post_deploy_smoke.py https://cerebrumdev-backend.onrender.com` → `/health` **404**. Against `https://api.cerebrum-dev.com`: `[LIVE] health`, `[DEAD] redis`, `[LIVE] register`, `[DEAD] session create` (403 `email_not_verified`). Gate requires every kernel `[LIVE]`. |
| 3. Readiness & ops | **FAIL** | `/ready` 200 on custom domain. `/version` **404**. Render service plan is **free** (blueprint says `starter`). `healthCheckPath: /ready`. No `cerebrumdev-redis`. Last backup `ok: false` at 2026-08-16T03:00:00Z. |
| 4. Auth / security | **PASS** | Unauthenticated `/v1/sessions/` → 401. Unauthenticated factory draft → 401. Register returns `email_verified: false`; session/me/billing 403 until verify. `ACCOUNTS_EXPOSE_DEV_TOKENS` blueprint default `0`. Residual: `BILLING_ENFORCEMENT=false`. |
| 5. CI / quality | **PASS** | Latest `master` CI run `31833110957` **success** (2026-08-14T19:24:34Z) for `#151`. |
| 6. Data | **FAIL** | Postgres `cerebrumdev-accounts` exists (`dpg-d9vlh6c9v7es73b53l9g-a`) on **free**, `expiresAt: 2026-09-13`. No Factory Key Value instance (only `the-fork-redis`). Nightly backup reported failed. |
| 7. Factory vs product | **PASS** | No runnable `backend/app/retailops/` tree. Generated products remain sibling repos. |
| 8. Frontend production | **PASS** | `https://cerebrum-dev.com` 200 SPA. Legacy `https://cerebrumdev-frontend.onrender.com` **404** (live slug is `cerebrumdev-frontend-kkz2.onrender.com`). |
| 9. Observability | **NOT VERIFIED** | `SENTRY_DSN` / `VITE_SENTRY_DSN` are dashboard secrets; not probed. Request-level evidence not collected this pass. |
| 10. Docs vs reality | **FAIL** | README still lists `cerebrumdev-backend.onrender.com` and `cerebrumdev-frontend.onrender.com`, both 404. Smoke default URL is the dead host. AGENTS.md deploy gate still points there. |

---

## Top blockers (production claim)

1. **Deploy-gate smoke fails.** Documented URL 404s. Live API fails Redis + session-create kernels.
2. **Redis unwired.** `/health` → `redis.configured: false`. Blueprint `cerebrumdev-redis` is not in the workspace; rate-limit / shared state assumed by smoke is absent.
3. **Render free web service.** MCP `plan: free` for `cerebrumdev-backend` (`srv-d9ta2pad0e5s738lllpg`). Free web services spin down after 15 minutes — not production.
4. **Accounts Postgres is free and time-boxed.** Expires 2026-09-13. Not a production database.
5. **Backup job failed** (`/ready` `details.last_backup.ok: false`).
6. **README / AGENTS.md / smoke default URL** still advertise hosts that 404 after the custom-domain cutover.

---

## What is already production-grade

- Git pin: live Render commit equals `origin/master` (`442b62f`).
- Custom domains: `https://cerebrum-dev.com` (UI), `https://api.cerebrum-dev.com` (API `/health` 200 `status=ok`, `/ready` 200, storage ok, Blocks 200, `llm_configured: true`, `llm_mock: false`).
- Auth fail-closed on protected routes; email verification is actually enforced on the live API (register `verification.mode=smtp`, `email_sent: true`).
- CI required path on `master` is green.
- Factory/product boundary held (no RetailOps runtime in this repo).

---

## Commands and outcomes

| Command / URL | Outcome |
| --- | --- |
| `GET https://cerebrumdev-backend.onrender.com/health` | **404** |
| `GET https://cerebrumdev-backend.onrender.com/ready` | **404** |
| `GET https://api.cerebrum-dev.com/health` | **200** `status=ok`, `redis.configured: false` |
| `GET https://api.cerebrum-dev.com/ready` | **200** `status=ready`; `last_backup.ok: false` |
| `GET https://api.cerebrum-dev.com/version` | **404** |
| `GET https://cerebrum-dev.com` | **200** SPA |
| `GET https://cerebrumdev-frontend.onrender.com` | **404** |
| `GET https://cerebrumdev-backend-goia.onrender.com/health` | **200** same body as custom domain |
| `python scripts/post_deploy_smoke.py https://cerebrumdev-backend.onrender.com` | **FAIL** 3 dead (health, redis, register/404) |
| `python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com` | **FAIL** redis + session create |
| `POST /v1/auth/register` on live API | **201** `email_verified: false` |
| `POST /v1/sessions/` with that token | **403** `email_not_verified` |
| `GET /v1/sessions/` unauthenticated | **401** |
| Render MCP `list_services` | backend `srv-d9ta2pad0e5s738lllpg` **free**, slug `cerebrumdev-backend-goia` |
| Render MCP `list_deploys` | live commit `442b62f` |
| Render MCP `list_key_value` | **no** `cerebrumdev-redis` |
| Render MCP `list_postgres_instances` | `cerebrumdev-accounts` **free**, expires 2026-09-13 |
| `gh run list --branch master --limit 1` | CI **success** |

Machine-readable copy: [`artifacts/production_readiness_audit.json`](../../artifacts/production_readiness_audit.json).

---

## Not in this PR

No runtime changes. Fixing Redis, plan, smoke URL, and backup belongs in follow-up PRs plus dashboard work — not a docs rewrite of the live facts.
