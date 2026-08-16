# CerebrumDev.ai — production-readiness audit

**Repository:** [bopoadz-del/CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai) (the Factory). Not Cerebrum-Steward.  
**Auditor window (UTC):** 2026-08-16T22:12Z–22:14Z  
**Prior pin:** 2026-08-16T21:37Z–21:41Z (NO-GO; redis + unverified session + 404 smoke host).  
**Rule:** PASS / FAIL / NOT VERIFIED only. No readiness %. Deterministic or fallback success is not production evidence. Live evidence required.

| Pin | Value |
| --- | --- |
| Local HEAD / `origin/master` | `a9b0b5e61bb6c6e06e02936c14a399fe4b401d05` |
| Live Render backend commit | `a9b0b5e61bb6c6e06e02936c14a399fe4b401d05` (`dep-da13anlg1s2s73bvl1r0`, status `live`, finished 2026-08-16T22:12:07Z) |
| Live Render frontend commit | same SHA (`dep-da13bsgu01pc739ctol0`, finished 2026-08-16T22:13:22Z) |
| Master CI | [PR #153](https://github.com/bopoadz-del/CerebrumDev.ai/pull/153) required checks **success** (Backend pytest, Frontend build+lint, Production Docker build) |

---

## Verdict

| Claim | Result |
| --- | --- |
| **Production** | **NO-GO** |
| **Unattended public demo** | **NO-GO** |
| **Owner-supervised walkthrough** | Factory loop is `[LIVE]` on the custom-domain API with a smoke-gate principal. Redis is still unwired. Render **free** web + free Postgres remain. **Not** a production claim. |

Code and SHA pin moved. Live smoke still exits 1 on Redis. Billing/plan upgrades cannot be applied through Render MCP (`update_service` / plan change is not in the tool set).

---

## Domain scores

| # | Domain | Status | Live evidence |
| --- | --- | --- | --- |
| 1 | Deploy pin (`origin/master` vs live Render SHA) | **PASS** | Backend `srv-d9ta2pad0e5s738lllpg` live commit equals `origin/master`. Frontend `srv-d9ta36v40ujc73dsmhkg` same. Auto-deploy trigger `commit` on `master`. |
| 2 | Live smoke / kernels `[LIVE]` | **FAIL** | See [Mandatory live gate](#mandatory-live-gate). Every kernel except Redis printed `[LIVE]`, including LLM drafting (not fallback). Smoke exit 1 on Redis. |
| 3 | Readiness & ops | **FAIL** | `/ready` HTTP 200. `/version` HTTP **200** `git_sha=a9b0b5e…`. `HEAD /ready` HTTP **200**. Uvicorn binds `0.0.0.0:$PORT`. Service **plan: free**; MCP `get_service` shows **no disk**. `/ready` `last_backup.ok: true` (postgres engine, `pg_dump_available: true` at 2026-08-16T22:12:00Z). Free-plan spin-down and no disk keep this FAIL. |
| 4 | Auth / security | **PASS** | Unauth writes 401. Public register + unverified session → **403** `email_not_verified`. Smoke uses `SMOKE_GATE_TOKEN` / `/v1/auth/smoke-login`; public verification stays on. CORS allowlist observed. |
| 5 | CI on latest master | **PASS** | SHA `a9b0b5e` jobs on #153: Backend pytest, Frontend build+lint, Production Docker build — all **success**. |
| 6 | Data / migrations | **FAIL** | Accounts on Postgres; nightly/bootstrap dump now succeeds (`pg_dump` in the image). Instance `dpg-d9vlh6c9v7es73b53l9g-a` plan **free**, `expiresAt: 2026-09-13T18:04:09Z`. Sessions/Chroma still under `/app/storage` on an ephemeral free filesystem. |
| 7 | Factory vs product confusion | **PASS** | No runnable `backend/app/retailops/` tree. RetailOps remains provenance docs + sibling product repo. |
| 8 | Frontend production | **PASS** | `https://cerebrum-dev.com` and `https://www.cerebrum-dev.com` HTTP 200. Live slug `https://cerebrumdev-frontend-kkz2.onrender.com` 200. `VITE_API_URL` set to `https://api.cerebrum-dev.com` and frontend rebuilt on `a9b0b5e`. |
| 9 | Observability / safe errors | **FAIL** | `/health` `sentry.configured: false`. `/version` `sentry_configured: false`. No `SENTRY_DSN` / `VITE_SENTRY_DSN` in env. Hooks are wired and no-op without a DSN; that is not live Sentry. |
| 10 | Docs vs reality | **PASS** | README, AGENTS.md, and `scripts/post_deploy_smoke.py` default to `https://api.cerebrum-dev.com` / `https://cerebrum-dev.com`. Residual: `render.yaml` still declares backend `starter` + disk; live service is free / no disk (called out in domain 3, not a 404-host lie). |

Machine-readable copy: [`artifacts/cerebrumdev_production_readiness.json`](../../artifacts/cerebrumdev_production_readiness.json).

---

## Mandatory live gate (AGENTS.md)

```bash
python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com
```

Requires `SMOKE_GATE_TOKEN` (Render secret) or a verified `SMOKE_EMAIL`/`SMOKE_PASSWORD`. Public email verification stays fail-closed.

| Target | Timestamp (UTC) | Result |
| --- | --- | --- |
| `python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com` | 2026-08-16T22:14Z | **SMOKE FAIL** (exit 1) — Redis only |

Smoke transcript against the live custom-domain API (no tokens logged):

```
post-deploy smoke against https://api.cerebrum-dev.com
[LIVE] health endpoint — status=ok
[DEAD] redis rate limiting configured — redis={'configured': False}
[LIVE] version endpoint — http=200 sha=a9b0b5e61bb6
[LIVE] auth register — http=201
[LIVE] unverified session denied — http=403
[LIVE] session create — http=200
[LIVE] chat blueprint event — sse_bytes=5773
[LIVE] LLM drafting (not fallback) — caps=5 populated=5 (fallback fingerprint: caps=2 populated=0)
[LIVE] approve -> generation event
[LIVE] generation recorded — product_id=winery-management
[LIVE] export zip — http=200 files=199
[LIVE] grounding (no invented deploy/URL)
[LIVE] cross-account isolation — http=404 (expect 404)
[LIVE] billing status structured — http=200
[LIVE] billing checkout honest — http=503 (503 stripe_not_configured or real url)
SMOKE FAIL: 1 dead kernel(s): redis rate limiting configured
```

LLM / generate / grounding / isolation / billing kernels are **LIVE** on this run. Redis is **FAIL**. Deterministic fallback was not accepted (populated capabilities were 5, not the fallback fingerprint).

---

## Infra follow-ups from this window

Render MCP workspace **My Workspace** `tea-d9rteq2jnfac738dnc70`:

| Resource | Id | Plan / note |
| --- | --- | --- |
| Web `cerebrumdev-backend` | `srv-d9ta2pad0e5s738lllpg` | **free**, slug `cerebrumdev-backend-goia` |
| Static `cerebrumdev-frontend` | `srv-d9ta36v40ujc73dsmhkg` | slug `cerebrumdev-frontend-kkz2` |
| Postgres `cerebrumdev-accounts` | `dpg-d9vlh6c9v7es73b53l9g-a` | **free**, expires 2026-09-13 |
| Key Value `cerebrumdev-redis` | `red-da131ae1egvs739s8ihg` | **starter**, Oregon, status `available`. **Not wired:** MCP `get_key_value` does not return the connection string, so `REDIS_URL` is still unset on the web service. Paste Internal URL from the Redis dashboard onto the backend, then redeploy. |

Render MCP **cannot** change web/Postgres instance plan (no update-plan tool). Do not treat `render.yaml` `starter` + disk as live. Dashboard upgrades required:

- Backend web: Starter (or always-on) + disk `cerebrumdev-storage` at `/app/storage`
- Postgres: paid plan matching the blueprint (`basic-256mb`) before **2026-09-13** expiry

Sentry: set `SENTRY_DSN` (backend) and `VITE_SENTRY_DSN` (frontend build) to real project DSNs. Do not invent a DSN.

---

## Top blockers (must fix before any production claim)

1. **Redis unwired.** Instance exists (`cerebrumdev-redis`, starter). `/health` `redis.configured: false`. Smoke DEAD until `REDIS_URL` is the Internal connection string.
2. **Render free web service.** Spin-down after 15 minutes idle. No persistent disk. Session JSON, uploads, Chroma, and backup copies under `/app/storage` are ephemeral.
3. **Accounts Postgres is free and expires 2026-09-13.** Alembic and `pg_dump` are wired; durability/plan are not.
4. **Sentry is not live.** Hooks no-op without DSNs.

Do not “fix” this by weakening CI or by accepting deterministic architect fallback as `[LIVE]`.

---

## What landed in #153 (not a GO)

- Canonical live hosts in README / AGENTS.md / smoke default.
- `GET /version` reports `RENDER_GIT_COMMIT`.
- `postgresql-client` in the production image; `/ready` backup probe is honest and now `ok: true` after a real dump.
- Production smoke gate (`SMOKE_GATE_TOKEN`) so a verified test principal can run the factory loop without disabling public email verification.
- Optional Sentry remains no-op without `SENTRY_DSN`.

---

## What is already solid (not a GO)

- Live SHA equals `origin/master` (`a9b0b5e`).
- Custom domains answer: UI `https://cerebrum-dev.com`, API `https://api.cerebrum-dev.com`.
- Auth fail-closed on protected routes; email verification enforced for public users; SMTP sends.
- Factory LLM loop `[LIVE]` (draft → approve → generate → zip → grounding → isolation).
- CI on `master` is green.
- No RetailOps runtime in this repo.
