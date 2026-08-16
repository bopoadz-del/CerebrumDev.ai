# CerebrumDev.ai — production-readiness audit

**Repository:** [bopoadz-del/CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai) (the Factory). Not Cerebrum-Steward.  
**Auditor window (UTC):** 2026-08-16T21:37Z–21:41Z  
**Rule:** PASS / FAIL / NOT VERIFIED only. No readiness %. Deterministic or fallback success is not production evidence. Live evidence required.

| Pin | Value |
| --- | --- |
| Local HEAD | `442b62f5d9a14b9247722320aadbd21ff6be0638` |
| `origin/master` | `442b62f5d9a14b9247722320aadbd21ff6be0638` |
| Live Render backend commit | `442b62f5d9a14b9247722320aadbd21ff6be0638` (`dep-d9vmms6417fc73a2qiig`, status `live`, finished 2026-08-14T19:25:11Z) |
| Live Render frontend commit | same SHA (`dep-d9vmms6417fc73a2qj3g`, finished 2026-08-14T19:24:50Z) |
| Master CI | [run 31833110957](https://github.com/bopoadz-del/CerebrumDev.ai/actions/runs/31833110957) **success** (push of `#151`) |

---

## Verdict

| Claim | Result |
| --- | --- |
| **Production** | **NO-GO** |
| **Unattended public demo** | **NO-GO** |
| **Owner-supervised walkthrough** | Site and API answer on custom domains; factory loop still not `[LIVE]` without a verified inbox. **Not** a production claim. |

SHA pin and CI are green. That does not make the service production-ready: the AGENTS.md smoke gate fails, Redis is unwired, the backend is a Render **free** web service (spin-down, no disk), accounts Postgres is **free** and expires **2026-09-13**, nightly backup cannot run `pg_dump`, and the documented `*.onrender.com` hostnames 404.

---

## Domain scores

| # | Domain | Status | Live evidence |
| --- | --- | --- | --- |
| 1 | Deploy pin (`origin/master` vs live Render SHA) | **PASS** | Backend `srv-d9ta2pad0e5s738lllpg` live deploy commit equals `origin/master`. Frontend `srv-d9ta36v40ujc73dsmhkg` same. Auto-deploy trigger `commit` on `master`. |
| 2 | Live smoke / kernels `[LIVE]` | **FAIL** | See [Mandatory live gate](#mandatory-live-gate). Documented smoke host 404s. Live custom-domain smoke: `[DEAD] redis`, `[DEAD] session create`. LLM / generate / grounding / isolation kernels were not reached. |
| 3 | Readiness & ops | **FAIL** | `/ready` HTTP 200 on live API. `/version` HTTP **404**. Uvicorn binds `0.0.0.0:10000` (Render `PORT`). Service **plan: free**; MCP `get_service` shows **no disk** despite `render.yaml` declaring `cerebrumdev-storage`. `/ready` `last_backup.ok: false`. `HEAD /ready` → 405 (Render GET health checks still pass). |
| 4 | Auth / security | **PASS** | Unauth writes 401. CORS allowlist observed. No demo-open principal on live (no credential → 401, not a `dev` bypass). Register does **not** return `dev_verification_token`. `backend/.env` is gitignored; CI `scan_secrets.py` is on the required path. Residual: live `ACCOUNTS_REQUIRE_VERIFIED_EMAIL` is on (stricter than `render.yaml` value `"0"`); `BILLING_ENFORCEMENT` remains off in the blueprint. |
| 5 | CI on latest master | **PASS** | SHA `442b62f` jobs: Backend pytest, Frontend build+lint, Production Docker build — all **success**. |
| 6 | Data / migrations | **FAIL** | Boot logs `alembic.runtime.migration` `Context impl PostgresqlImpl` at 2026-08-14T19:24:57Z — accounts **are** on Postgres. Instance `dpg-d9vlh6c9v7es73b53l9g-a` plan **free**, `expiresAt: 2026-09-13T18:04:09Z`. Nightly backup log 2026-08-16T03:00:00Z: `nightly backup FAILED: accounts dump failed: [Errno 2] No such file or directory: 'pg_dump'`. Sessions/Chroma live under `/app/storage` on an ephemeral free filesystem. |
| 7 | Factory vs product confusion | **PASS** | No runnable `backend/app/retailops/` tree. RetailOps remains provenance docs + sibling product repo. Kernel contract test forbids `retailops` imports. |
| 8 | Frontend production | **PASS** | `https://cerebrum-dev.com` and `https://www.cerebrum-dev.com` HTTP 200 SPA (`index-89PSwFs6.js`, `Last-Modified: Fri, 14 Aug 2026 19:24:50 UTC`). Live slug `https://cerebrumdev-frontend-kkz2.onrender.com` 200. Documented `https://cerebrumdev-frontend.onrender.com` **404**. Production JS bakes `https://cerebrumdev-backend-goia.onrender.com` (not `VITE_API_URL=https://api.cerebrum-dev.com` from `render.yaml`). |
| 9 | Observability / safe errors | **FAIL** | Frontend bundle (163696 bytes from www) contains **zero** `sentry` / `Sentry` / `ingest.sentry` strings — `VITE_SENTRY_DSN` was not set at build. Backend boot logs 2026-08-14T19:24:32Z–19:25:08Z show alembic + `Uvicorn running on http://0.0.0.0:10000` and **no** `Sentry initialized`. 401/403 bodies are structured `{"detail": "..."}` with no traceback. |
| 10 | Docs vs reality | **FAIL** | README, AGENTS.md, `scripts/post_deploy_smoke.py` default, and `docs/audits/REGISTERED_BUT_DEAD_AUDIT.md` still advertise `cerebrumdev-backend.onrender.com` / `cerebrumdev-frontend.onrender.com` / `cerebrum-blocks.onrender.com` — all **404**. Blueprint says backend `starter` + disk + `cerebrumdev-redis`; live is free, no disk, no Factory Redis. July 24 audit claimed Redis `[LIVE]`; live `/health` is `redis.configured: false`. SMTP is live (`verification.mode=smtp`, `email_sent: true`), not the parked-dev-token story. |

Machine-readable copy: [`artifacts/cerebrumdev_production_readiness.json`](../../artifacts/cerebrumdev_production_readiness.json).

---

## Mandatory live gate (AGENTS.md)

Command required by AGENTS.md:

```bash
python scripts/post_deploy_smoke.py https://cerebrumdev-backend.onrender.com
```

| Target | Timestamp (UTC) | Result |
| --- | --- | --- |
| `GET https://cerebrumdev-backend.onrender.com/health` | 2026-08-16T21:38:13Z | **HTTP 404** (host does not exist; smoke cannot print `[LIVE]`) |
| `python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com` | 2026-08-16T21:39:06Z | **SMOKE FAIL** (exit 1) |

Smoke transcript against the live custom-domain API:

```
post-deploy smoke against https://api.cerebrum-dev.com
[LIVE] health endpoint — status=ok
[DEAD] redis rate limiting configured — redis={'configured': False}
[LIVE] auth register — http=201
[DEAD] session create — http=403
SMOKE FAIL: 2 dead kernel(s): redis rate limiting configured, session create
```

Follow-up (2026-08-16T21:39:49Z), no tokens logged: `POST /v1/sessions/` with the register `login_token` → **403** `{"detail":"email_not_verified"}`. Same for `/v1/auth/me` and `/v1/billing/status`. Register body: `email_verified: false`, `verification.mode: smtp`, `email_sent: true`, no `dev_verification_token`.

Kernels after session create (LLM drafting, approve→generate, zip export, grounding, cross-account isolation, billing checkout honesty) are **NOT VERIFIED**. They were not reached. They are not `[LIVE]`.

---

## Re-verify `REGISTERED_BUT_DEAD_AUDIT.md` (dated 2026-07-24)

That document is **stale**. It was written against `https://cerebrumdev-backend.onrender.com`, which is now 404. Workspace id cited by operators (`tea-d2gv3pf5r7bs73fh82eg`) 404s on Render MCP; live workspace is **My Workspace** `tea-d9rteq2jnfac738dnc70`. Factory services were **recreated 2026-08-11** with new slugs (`*-goia`, `*-kkz2`).

| Kernel (from 2026-07-24 table) | 2026-07-24 claim | 2026-08-16 re-verify |
| --- | --- | --- |
| Chat LLM / architect / generate / grounding / isolation | LIVE | **NOT VERIFIED** (session create 403) |
| Billing honesty | LIVE (honest 503) | **NOT VERIFIED** (403 before billing) |
| Redis rate limiting | LIVE | **FAIL** (`redis.configured: false`; no `cerebrumdev-redis`) |
| Auth on protected routes | LIVE | **PASS** (401 unauth) |
| SMTP | PARKED-honest (dev tokens) | **PASS as live mail** (smtp, `email_sent: true`; no dev token) |
| Resident engineer / workbench | PARKED by design | **PASS (honest parked)** — `/health` flags false |
| CI wiring | EXISTS | **PASS** |
| Frontend API-key pair | LIVE (bundled `VITE_API_KEY`) | Frontend src uses login tokens, not `VITE_API_KEY` |
| AGENTS.md deploy gate | WIRED | Gate still points at a **404** host |

---

## Probe log (HTTP)

All times UTC 2026-08-16 unless noted.

| URL | Status | Notes |
| --- | --- | --- |
| `https://cerebrumdev-backend.onrender.com/health` | 404 | Documented smoke URL |
| `https://cerebrumdev-backend.onrender.com/ready` | 404 | |
| `https://cerebrumdev-backend.onrender.com/version` | 404 | |
| `https://cerebrumdev-backend-goia.onrender.com/health` | 200 | `status=ok`, `redis.configured: false`, storage `/app/storage` ok |
| `https://cerebrumdev-backend-goia.onrender.com/ready` | 200 | `status=ready`; Blocks 200; `llm_configured: true`; `llm_mock: false`; `api_key_configured: true`; `last_backup.ok: false` at 2026-08-16T03:00:00.001627+00:00 |
| `https://cerebrumdev-backend-goia.onrender.com/version` | 404 | Factory itself has no `/version` (generator templates do) |
| `https://api.cerebrum-dev.com/health` | 200 | Same body as goia slug |
| `https://api.cerebrum-dev.com/ready` | 200 | Same body as goia slug |
| `https://api.cerebrum-dev.com/version` | 404 | |
| `https://cerebrum-dev.com/` | 200 | SPA; apex redirects asset fetches to www |
| `https://www.cerebrum-dev.com/` | 200 | SPA |
| `https://cerebrumdev-frontend.onrender.com/` | 404 | Documented frontend URL |
| `https://cerebrumdev-frontend-kkz2.onrender.com/` | 200 | Live slug |
| `https://cerebrum-blocks.onrender.com/health` | 404 | Documented Blocks URL |
| `https://cerebrum-blocks-10ug.onrender.com/health` | 200 | Live Blocks slug |
| `GET /v1/auth/me` (no auth) | 401 | `Invalid or missing API key` |
| `POST /v1/sessions/` (no auth) | 401 | |
| `POST /v1/factory/product/draft` (no auth) | 401 | |
| `GET /v1/billing/status` (no auth) | 401 | |
| `GET /v1/domains/virgin` (no auth) | 401 | |
| `POST /v1/auth/register` | 201 | smtp verification; no secret logged here |
| CORS `Origin: https://cerebrum-dev.com` → API | 200 | `access-control-allow-origin: https://cerebrum-dev.com` |
| CORS `Origin: https://www.cerebrum-dev.com` → API | 200 | ACAO echoes www |
| CORS `Origin: https://cerebrumdev-frontend-kkz2.onrender.com` → API | 200 | ACAO echoes slug |
| CORS `Origin: https://evil.example` → API | 200 | **no** ACAO |
| CORS `Origin: https://cerebrumdev-frontend.onrender.com` → API | 200 | **no** ACAO (legacy origin not live-allowed) |

Render MCP (workspace `tea-d9rteq2jnfac738dnc70`):

| Resource | Id | Plan / note |
| --- | --- | --- |
| Web `cerebrumdev-backend` | `srv-d9ta2pad0e5s738lllpg` | **free**, slug `cerebrumdev-backend-goia`, `healthCheckPath: /ready`, created 2026-08-11T04:13:25Z |
| Static `cerebrumdev-frontend` | `srv-d9ta36v40ujc73dsmhkg` | slug `cerebrumdev-frontend-kkz2` |
| Postgres `cerebrumdev-accounts` | `dpg-d9vlh6c9v7es73b53l9g-a` | **free**, expires 2026-09-13, status available |
| Key Value `cerebrumdev-redis` | — | **absent** (only `the-fork-redis` exists in the workspace) |

Dockerfile CMD is `uvicorn ... --host 0.0.0.0 --port ${PORT}` after `alembic upgrade head`. Boot log confirms both.

---

## Top blockers (must fix before any production claim)

1. **AGENTS.md smoke gate fails.** Documented host 404. Live API fails Redis + unverified-email session create. Every kernel must print `[LIVE]`; it does not.
2. **Redis missing.** Blueprint `cerebrumdev-redis` is not provisioned. `/health` `redis.configured: false`. Smoke treats that as a dead kernel. Rate limits fall back to in-process memory (lost on spin-down).
3. **Render free web service.** Spin-down after 15 minutes idle. No persistent disk on the service. Session JSON, uploads, Chroma, and backup copies under `/app/storage` are ephemeral.
4. **Accounts Postgres is free and expires 2026-09-13.** Not a production database. Alembic is wired; durability is not.
5. **Nightly backup is broken.** `pg_dump` is not in the `python:3.11-slim` image. `/ready` reports `last_backup.ok: false`.
6. **Docs advertise dead hostnames** (README, AGENTS.md, smoke default, July 24 audit, Blocks URL).
7. **No `/version`** on the Factory API, so operators cannot confirm SHA without Render.
8. **Sentry is not live** in the frontend bundle or backend boot logs.

Do not “fix” this by weakening CI or by accepting deterministic architect fallback as `[LIVE]`.

---

## What is already solid (not a GO)

- Live SHA equals `origin/master`.
- Custom domains answer: UI `https://cerebrum-dev.com`, API `https://api.cerebrum-dev.com`.
- Auth fail-closed on protected routes; email verification is enforced on the live API; SMTP actually sends.
- CORS is an allowlist, not `*`.
- CI on `master` is green (pytest, frontend, production Docker image).
- No RetailOps runtime in this repo.
- `/ready` is the Render health check and returns 503 when storage/API-key checks fail (live currently 200).

---

## Out of scope / not done

- No dashboard secret values were read or written.
- No merge of this audit until CI on the audit PR is green.
- No runtime/config changes in this PR (docs/audit only). Follow-ups: paid plan + disk, provision Factory Redis, install `pg_dump` or stop claiming backups, point docs/smoke at `https://api.cerebrum-dev.com`, add `/version`, set Sentry DSNs, align smoke with email-verification, rebuild frontend with `VITE_API_URL=https://api.cerebrum-dev.com`.
