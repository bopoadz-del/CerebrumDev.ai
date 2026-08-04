# Pilot / Factory Runbook

One-page operations guide for CerebrumDev.ai factory and Automotive Safety Intelligence pilot.

## Deploy (factory)

1. Ensure Render Blueprint uses [render.yaml](../render.yaml).
2. Set secrets in Render dashboard (never commit):
   - `CEREBRUM_API_KEY` — must match Cerebrum-Blocks store key (`sync: false`)
   - `KIMI_API_KEY` (or `CEREBRUM_LLM_API_KEY`) — Kimi/Moonshot is the only LLM provider
   - `VITE_API_KEY` on the static site — must match backend `CEREBRUM_DEV_API_KEY`
3. Deploy backend first, copy generated `CEREBRUM_DEV_API_KEY`, set frontend `VITE_API_KEY`, redeploy frontend.
4. Confirm `GET /health` and `GET /ready` return healthy.

## Deploy (automotive pilot package)

```bash
python -m scripts.generate_automotive_platform --output generated/automotive-safety-intelligence
python -m scripts.deploy_automotive_pilot --package generated/automotive-safety-intelligence
cd generated/automotive-safety-intelligence
docker compose up --build
```

Smoke:

- `GET /health`
- `GET /ready`
- `GET /metrics`

## Rollback

- **Render:** Redeploy previous successful deploy from the Render dashboard (Manual Deploy → select prior image/commit).
- **Pilot pack activation:** `POST /v1/admin/automotive-core/rollback`
- **Compose:** `git checkout <prior-sha>` in the deploy repo and recreate containers.

## Key rotation

1. Rotate `CEREBRUM_DEV_API_KEY` / `VITE_API_KEY` together.
2. Rotate `CEREBRUM_API_KEY` to match Cerebrum-Blocks.
3. Rotate `DATA_ENCRYPTION_KEY` only with a re-encrypt plan for Drive tokens.
4. Restart backend after rotation; rebuild frontend when `VITE_*` changes.

## Disk / backup (`/app/storage`)

Render disk is ephemeral across service deletion but persists across deploys while attached.

- Snapshot: copy `/app/storage` via one-off job or `render disk` backup before destructive changes.
- Restore: stop service, restore files to mount path, restart.
- Pilot Postgres: use managed DB backups / `pg_dump` before pack rebuilds.

### Automated backups

An in-process scheduler inside the web service (03:00 UTC nightly, plus a
bootstrap snapshot on first boot when no archive exists) snapshots the accounts
database plus `uploads/`, `sessions/` and `chroma/` into `$BACKUP_DIR`
(default `$STORAGE_PATH/backups`), keeping 14 archives. It is deliberately not
a Render cron job: cron jobs cannot mount the persistent disk, so only the
service that owns the disk can back it up. Status surfaces non-gating at
`/ready` under `details.last_backup`.

The accounts snapshot uses SQLite's online backup API rather than a file copy —
copying a live database can capture a torn write, producing an archive that
restores cleanly and is quietly corrupt. Every snapshot is opened and
integrity-checked before the job reports success.

**What this does not cover:** by default the copy is on the same disk as the
original. That protects against logical loss (accidental delete, bad migration)
but not against losing the disk. For that, point `BACKUP_DIR` at another
volume, or move accounts to managed Postgres and use its point-in-time
recovery.

**Rehearse the restore.** An untested backup is not a backup:

```bash
python -m scripts.backup_cli drill    # snapshot -> restore -> verify row counts
```

Non-zero exit means the archive cannot be put back. Run it now, and again after
any change to the storage layout.

**Restore for real:**

```bash
python -m scripts.backup_cli list
python -m scripts.backup_cli restore <archive> /tmp/restore-check
# inspect, confirm counts, THEN promote deliberately
```

Restore never writes over the live location, so a drill cannot destroy
production. Promoting is a separate manual act.

## Moving accounts to Postgres

SQLite is one file with one writer: under public signup load it returns
`database is locked` as HTTP 500s, and it has no point-in-time recovery.

**The switch copies nothing.** Setting `ACCOUNTS_DATABASE_URL` against an empty
database loses every account — and the app boots fine reporting no users, so
nobody notices until a customer cannot log in.

```bash
# 1. sync the blueprint so cerebrumdev-accounts is provisioned
# 2. create the schema in the new database
ACCOUNTS_DATABASE_URL=<url> python -m alembic upgrade head
# 3. copy the data and check it landed
ACCOUNTS_DATABASE_URL=<url> python -m scripts.migrate_accounts_to_postgres --verify
# 4. only now set ACCOUNTS_DATABASE_URL on the web service
```

The migration refuses a non-empty target unless forced, and `--verify` re-reads
both sides and compares counts rather than trusting that the inserts ran.

## Health and alerting

- `/ready` is the platform health check. It probes real dependencies and returns
  **503** when they are broken, so Render can act on it.
- `/health` is informational and always 200 — never point a health check at it.
- A failed migration now stops the boot instead of leaving a service running on
  the wrong schema. Render shows a failed deploy and keeps the previous version.

**Gap:** `notifyOnFail: default` is deploy-failure email only. Nothing pages
anyone for an OOM, a hang, or a full disk. Add an external uptime check against
`/ready` before opening registration, or the first signal of an outage is a
customer complaining.

## Before opening registration

- [ ] `backup_cli drill` passes.
- [ ] An external uptime check points at `/ready`.
- [ ] A mail provider is set (`RESEND_API_KEY`), **then**
      `ACCOUNTS_REQUIRE_VERIFIED_EMAIL=1`. The second without the first locks
      out every new account.
- [ ] `ACCOUNTS_EXPOSE_DEV_TOKENS` is unset or `0` everywhere.
- [ ] `DATA_ENCRYPTION_KEY` is set, or you accept that Drive OAuth tokens and
      stored documents sit plaintext on disk.
- [ ] The Postgres cutover is decided before launch, not under load.
- [ ] Counsel has cleared `docs/legal/`.

## Provider failure

Kimi (Moonshot) is the only LLM provider — there is no cross-provider
fallback to switch to.

1. Check `/ready` and backend logs; confirm https://api.moonshot.ai is
   reachable and the key is valid.
2. Model-level fallback is automatic (`fallback_model` in
   `backend/app/core/llm_config.py`; override via `KIMI_FALLBACK_MODEL`).
3. While the provider is down: drafting falls back through Golden Steward /
   keyword mode with the mode disclosed on the blueprint; the coder ships
   honest stubs with reasons. RAG/admin read paths stay available.
