# CerebrumDev.ai — production-readiness audit

**Repository:** [bopoadz-del/CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai) (the Factory). Not Cerebrum-Steward.  
**Auditor window (UTC):** 2026-08-16T23:48Z–23:51Z  
**Prior pin:** 2026-08-16T22:12Z–22:14Z (NO-GO; Redis DEAD, free web, no disk).  
**Rule:** PASS / FAIL / NOT VERIFIED only. No readiness %. Deterministic or fallback success is not production evidence. Live evidence required.

| Pin | Value |
| --- | --- |
| Local HEAD / `origin/master` | `bffeeaeb06fa0dc6f2cb3e0b66387bf70c2a5867` |
| Live Render backend commit | `bffeeaeb06fa0dc6f2cb3e0b66387bf70c2a5867` (`dep-da14ofojo6nc73froil0`, status `live`, finished 2026-08-16T23:49:51Z) |
| Live Render frontend commit | same SHA (`dep-da14i5ur33ss73f97afg`, finished 2026-08-16T23:35:12Z) |
| Master CI | SHA `bffeeae` workflow **CI** run `31975976219` **success** (required jobs on that workflow) |

---

## Verdict

| Claim | Result |
| --- | --- |
| **Production** | **NO-GO** |
| **Unattended public demo** | **NO-GO** |
| **Owner-supervised walkthrough** | Factory loop is `[LIVE]` on the custom-domain API with a smoke-gate principal. Backend is **starter** with disk `cerebrumdev-storage` at `/app/storage`. Redis and Sentry are live. **Accounts are still Render free Postgres**, not Neon. **Not** a production claim. |

Disk is attached and live smoke exits 0. Production stays **NO-GO** because Factory accounts are **not** on Neon.

---

## Domain scores

| # | Domain | Status | Live evidence |
| --- | --- | --- | --- |
| 1 | Deploy pin (`origin/master` vs live Render SHA) | **PASS** | Backend `srv-d9ta2pad0e5s738lllpg` live commit equals `origin/master`. Frontend `srv-d9ta36v40ujc73dsmhkg` same. Auto-deploy trigger `commit` on `master`. |
| 2 | Live smoke / kernels `[LIVE]` | **PASS** | See [Mandatory live gate](#mandatory-live-gate). Smoke exit 0. Every kernel printed `[LIVE]`, including Redis and LLM drafting (not fallback). Billing checkout 503 `stripe_not_configured` is the honest PASS. |
| 3 | Readiness & ops | **PASS** | `/ready` HTTP 200. `HEAD /ready` HTTP 200. `/version` HTTP 200 `git_sha=bffeeae…`. Uvicorn binds `0.0.0.0:$PORT`. Service **plan: starter**; `numInstances: 1`. MCP `get_service` shows disk `dsk-da14odgu01pc739fkrvg` `cerebrumdev-storage` 1GB `/app/storage`. `/ready` `last_backup.ok: true` (postgres engine, `pg_dump_available: true` at 2026-08-16T23:49:44Z). |
| 4 | Auth / security | **PASS** | Unauth writes 401. Public register + unverified session → **403** `email_not_verified`. Smoke uses `SMOKE_GATE_TOKEN` / `/v1/auth/smoke-login`; public verification stays on. CORS allowlist observed. |
| 5 | CI on latest master | **PASS** | SHA `bffeeae` CI run `31975976219` **success**. |
| 6 | Data / migrations | **FAIL** | `ACCOUNTS_DATABASE_URL` is **present**. `/ready` `last_backup.engine` is `postgres`. Host classification of the live env value is **Render Postgres**, not `neon.tech`. Instance `dpg-d9vlh6c9v7es73b53l9g-a` plan **free**, `expiresAt: 2026-09-13T18:04:09Z`. No Factory Neon URI was in this chat or related transcripts. Neon MCP lists only project `the-fork` (`round-shadow-39311244`); that product DB was **not** reused. Sessions/Chroma now persist under `/app/storage` on the attached disk. |
| 7 | Factory vs product confusion | **PASS** | No runnable `backend/app/retailops/` tree. RetailOps remains provenance docs + sibling product repo. |
| 8 | Frontend production | **PASS** | `https://cerebrum-dev.com` follows to HTTP 200. `https://www.cerebrum-dev.com` HTTP 200. Live slug `https://cerebrumdev-frontend-kkz2.onrender.com` 200. `VITE_API_URL` is `https://api.cerebrum-dev.com`. |
| 9 | Observability / safe errors | **PASS** | `/health` `sentry.configured: true`. `/version` `sentry_configured: true`. `SENTRY_DSN` present on the backend. Frontend `VITE_SENTRY_DSN` was set and rebuilt on `dep-da14i5ur33ss73f97afg`. |
| 10 | Docs vs reality | **PASS** | This audit matches live evidence: starter, Redis, Sentry, disk attached, Neon **not** the accounts engine. |

Machine-readable copy: [`artifacts/cerebrumdev_production_readiness.json`](../../artifacts/cerebrumdev_production_readiness.json).

---

## Mandatory live gate (AGENTS.md)

```bash
python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com
```

Requires `SMOKE_GATE_TOKEN` (Render secret) or a verified `SMOKE_EMAIL`/`SMOKE_PASSWORD`. Public email verification stays fail-closed. `SMOKE_GATE_TOKEN` was already present on the backend (not printed; not rotated).

| Target | Timestamp (UTC) | Result |
| --- | --- | --- |
| `python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com` | 2026-08-16T23:51Z | **SMOKE PASS** (exit 0) |

Smoke transcript against the live custom-domain API (no tokens logged):

```
post-deploy smoke against https://api.cerebrum-dev.com
[LIVE] health endpoint — status=ok
[LIVE] redis rate limiting configured — redis={'configured': True, 'ok': True}
[LIVE] version endpoint — http=200 sha=bffeeaeb06fa
[LIVE] auth register — http=201
[LIVE] unverified session denied — http=403
[LIVE] session create — http=200
[LIVE] chat blueprint event — sse_bytes=4576
[LIVE] LLM drafting (not fallback) — caps=4 populated=4 (fallback fingerprint: caps=2 populated=0)
[LIVE] approve -> generation event
[LIVE] generation recorded — product_id=vineyard-management
[LIVE] export zip — http=200 files=161
[LIVE] grounding (no invented deploy/URL)
[LIVE] cross-account isolation — http=404 (expect 404)
[LIVE] billing status structured — http=200
[LIVE] billing checkout honest — http=503 (503 stripe_not_configured or real url)
SMOKE PASS: every kernel live.
```

LLM / generate / grounding / isolation / billing / Redis kernels are **LIVE**. Deterministic fallback was not accepted (populated capabilities were 4, not the fallback fingerprint).

---

## Infra follow-ups from this window

Render MCP workspace **My Workspace** `tea-d9rteq2jnfac738dnc70`:

| Resource | Id | Plan / note |
| --- | --- | --- |
| Web `cerebrumdev-backend` | `srv-d9ta2pad0e5s738lllpg` | **starter**, slug `cerebrumdev-backend-goia`, `numInstances: 1` |
| Disk `cerebrumdev-storage` | `dsk-da14odgu01pc739fkrvg` | 1GB, mount `/app/storage`. Attached via Render API `POST /v1/disks`; remount deploy `dep-da14ofojo6nc73froil0` **live**. |
| Static `cerebrumdev-frontend` | `srv-d9ta36v40ujc73dsmhkg` | slug `cerebrumdev-frontend-kkz2` |
| Postgres `cerebrumdev-accounts` | `dpg-d9vlh6c9v7es73b53l9g-a` | **free**, expires 2026-09-13. **This is still the live `ACCOUNTS_DATABASE_URL` host.** |
| Key Value `cerebrumdev-redis` | `red-da131ae1egvs739s8ihg` | **starter**, Oregon, status `available`. Wired: `/health` `redis.configured: true`, `ok: true`. |

Neon: **not wired**. Chat/transcripts contain no `postgresql://…neon.tech` URI. Neon org **CHADi** has one project, `the-fork` — not used for Factory accounts.

---

## Top blockers (must fix before any production claim)

1. **Factory accounts are not on Neon.** Live `ACCOUNTS_DATABASE_URL` classifies as Render Postgres `dpg-d9vlh6c9v7es73b53l9g-a` (free, expires 2026-09-13). Paste a Factory Neon URI (do not reuse `the-fork`) and merge it as `ACCOUNTS_DATABASE_URL` (`replace=false`).
2. **Stripe billing is honestly unconfigured.** Checkout 503 `stripe_not_configured` is correct, not a smoke fail.

Do not “fix” this by weakening CI, by pointing Factory at The Fork’s Neon project, or by accepting deterministic architect fallback as `[LIVE]`.

---

## What landed in this window (not a GO)

- Persistent disk `cerebrumdev-storage` attached at `/app/storage` (1GB). `numInstances` remains 1.
- Full live smoke exit 0; every kernel `[LIVE]`.
- Prior window already had starter plan, Redis, backend+frontend Sentry, and `SMOKE_GATE_TOKEN`.

---

## What is already solid (not a GO)

- Live SHA equals `origin/master` (`bffeeae`).
- Custom domains answer: UI `https://cerebrum-dev.com`, API `https://api.cerebrum-dev.com`.
- Auth fail-closed on protected routes; email verification enforced for public users.
- Factory LLM loop `[LIVE]` (draft → approve → generate → zip → grounding → isolation).
- Redis rate limiting `[LIVE]`.
- Sentry configured on backend `/health` and `/version`.
- CI on `master` is green.
- No RetailOps runtime in this repo.
