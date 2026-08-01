# Public Launch Readiness Register

Findings from a four-dimension audit of **production code only**
(`CerebrumDev.ai@origin/master b005fab`, `Cerebrum-Blocks@origin/main`), run
2026-08-01 ahead of opening the platform to public registration.

Nothing here credits the unmerged `claude/cerebrum-free-trial-readiness-4vwzmn`
branch. Where a fix exists only on that branch, it is marked **UNMERGED** —
meaning it is *not* protecting production today.

**Verdict: not ready to open registration.** The three items fixed below were
the reachable ones. The blockers that remain are mostly operational, and the
single largest is that there are no backups of anything.

---

## Fixed in this branch

| # | Finding | Where |
|---|---|---|
| C1 | **Arbitrary recursive delete.** `output_dir` from the request body reached `shutil.rmtree`. Any registered user could delete `/app/storage` — accounts DB, all sessions, all uploads. Found independently by three reviewers. | `factory/paths.py`, `factory/generator.py`, both generate routes |
| C2 | **Account takeover.** `/v1/auth/forgot-password` returned the live reset token to an unauthenticated caller whenever mail delivery failed — and it failed open both when unconfigured and on any transient provider error. | `routers/accounts.py` |
| C3 | **Fail-open auth.** The anonymous `dev` principal was the default whenever no master key was set, guarded only by `ENV == "production"` exactly. Not live (render.yaml pins it), but one typo away. | `core/auth.py` |

---

## Open — must close before launch

### Blockers

- **No backups. Of anything.** Every account, password hash, API key, upload
  and generated product lives on two 1 GB Render disks with no verified copy.
  The only backup cron in the workspace belongs to an unrelated repo and is
  suspended. **Nothing else on this page matters if this stands.**
- **All CDEV identity is SQLite on one unbacked disk.** `ACCOUNTS_DATABASE_URL`
  is unset, so `accounts_store` resolves to `/app/storage/accounts.db` —
  confirmed by `SQLiteImpl` in every production boot log. SQLite is also
  single-writer: public signup load produces `database is locked` as 500s.
- **Cross-tenant data access in Blocks.** `_namespace` is caller-supplied and
  unenforced on `origin/main`; `project_id` likewise, which reaches another
  project's pgvector corpus through the `knowledge` block. `/v1/memory/{action}`
  bypasses trust scope entirely. **Fix is UNMERGED** in PR #58.
- **Trust scope resolves every caller to the same tenant.** The live auth dict
  carries no `id`/`email`, so `enforce_trust_scope` substitutes
  `tenant_id="apikey:anonymous"` for everyone — the partition is real but the
  identity it partitions on is degenerate.
- **No Terms of Service, no Privacy Policy.** Drafts now in `docs/legal/`,
  requiring legal review. Open registration + email collection + Stripe without
  these is a compliance exposure.
- **No data export or deletion path.** No `delete_account` exists anywhere.
  Nothing expires. This is the GDPR/CCPA gap.

### Cost and abuse

- **`/product/draft` is an unmetered paid LLM call.** No quota check, no
  `max_tokens`, untruncated user brief, retries once against a fallback model.
  Unbounded spend per free account, and accounts are unlimited.
- **Registration is unlimited and instantly usable.** Email verification is off
  by default; every trial counter keys on `account_id` only, so quotas reduce to
  "how fast can you POST /register".
- **The auth rate limiter is keyed on `request.client.host`** with no proxy
  headers configured — behind Render that is not the end user. It is either
  globally shared (10 requests locks out login for everyone) or unevenly
  enforced. Do not naively trust `X-Forwarded-For` when fixing; bound the
  bucket dict too.
- **No request size limit anywhere on CDEV.** `upload.py` does
  `await file.read()` over an unbounded file list. Blocks gets this right
  (10 MB + allowlist + libmagic); CDEV has nothing.
- **Shared key into Blocks.** CDEV drives Blocks with one server-side key; if it
  holds the master value the tier is `unlimited` and block access control is
  bypassed for all CDEV traffic. **Verify which value is set.**

### Operational visibility

- **Migration failure is silent.** The Dockerfile CMD is
  `alembic upgrade head || echo ...` — a failed migration yields a *running*
  service on the wrong schema, `/health` still 200, no restart, no alert.
- **Health checks do not fail.** CDEV `/health` returns 200 even when
  `degraded`; Blocks `/health` returns `healthy` unconditionally with no probe
  at all. Render only reads the status code, so a broken disk reads as healthy.
  The useful endpoint, `/ready`, is not wired to `healthCheckPath`.
- **Nobody gets paged.** Both services are `notifyOnFail: default` — deploy
  failures only. No uptime check, no metric alert, no log alert. For an
  OOM, a hang, or a full disk, the first signal is a user complaining.
- **Capacity: 512 MB / 0.5 vCPU, single instance, disk attached.** Blocks idles
  at 32% with 7 of 64 blocks loaded; the first request routing to a
  `sentence_transformers` block imports torch and OOM-kills the only instance.
  Horizontal scaling is impossible while a disk is attached.

### Disclosure

- **Sentry on Blocks has no `before_send` and no body-size cap**, so
  `/v1/execute` bodies — which carry plaintext secrets for the `secrets`,
  `config` and `auth` blocks — go to a third-party SaaS on any exception.
- **Encryption at rest is off.** `DATA_ENCRYPTION_KEY` appears nowhere in
  `render.yaml`, so Google OAuth refresh tokens are written plaintext despite
  docstrings claiming otherwise; uploads never go through `file_crypto` at all.
- **Unauthenticated `/health` on Blocks** runs a subprocess and returns its
  stderr plus the raw value of `KIMI_CLI_PATH`. `/stats` re-exposes the block
  inventory that `/blocks` is deliberately auth-gated to protect.
- **Raw API keys stored plaintext** as a SQLite primary key in Blocks
  (`rate_limits.db`), including the master key.
- **Per-tier block access is a silent no-op** in Blocks: live tiers are
  `standard`/`unlimited`, the `Tier` enum only accepts `free|pro|enterprise`, so
  the check `ValueError`s into a bare `return`. The primary gate still holds —
  `code`/`sandbox`/`secrets` are not open — but `workbench` (returns raw
  subprocess output) is reachable by any issued key.

---

## Confirmed sound

Worth recording so nobody "fixes" these:

- **Stripe is correct** — webhook signature verified, 503 when unconfigured,
  trial/paid state not client-settable, account derived from the credential.
- **Session ownership holds** — cross-account access returns 404, not 403, so
  existence does not leak.
- **Password hashing** — PBKDF2-HMAC-SHA256, 200k iterations, per-user salt,
  constant-time verify. Reset revokes all login tokens.
- **Drive OAuth CSRF binding is real** — server-generated single-use `state`
  bound to the session.
- **No secret is committed** in either history, and no generated artifact
  embeds a factory credential — the packagers emit per-package random values.
- **No global exception handler**, so tracebacks do not reach clients.

---

## Owner actions — nobody else can do these

1. Merge PR #126 (CDEV) and PR #58 (Blocks). The tenant-isolation fix is in #58.
2. Set up a backup of `/app/storage` and `/app/data`, or move accounts to
   managed Postgres with PITR. Confirm whether Render retains disk snapshots.
3. Confirm what **Moonshot AI** does with submitted content — whether it is
   retained or trained on, and whether that can be disabled. The privacy policy
   cannot truthfully claim "we do not train on your content" until this is
   answered upstream.
4. Set `STORAGE_PATH=/app/data` on the live `cerebrum-blocks` service
   (`srv-d8rrorvavr4c73evhvi0`); the blueprint does not manage that service.
5. Rotate the Render API key that was pasted into a working session, and move
   the block-signing private key out of the ephemeral scratchpad.
6. Have counsel review `docs/legal/` and resolve every `[[NEEDS INPUT]]`.
