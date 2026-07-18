# Pilot / Factory Runbook

One-page operations guide for CerebrumDev.ai factory and Automotive Safety Intelligence pilot.

## Deploy (factory)

1. Ensure Render Blueprint uses [render.yaml](../../render.yaml).
2. Set secrets in Render dashboard (never commit):
   - `CEREBRUM_API_KEY` — must match Cerebrum-Blocks store key (`sync: false`)
   - `OLLAMA_API_KEY` / LLM keys as needed
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

## Provider failure

If Ollama/Moonshot/Qwen fails:

1. Check `/ready` and backend logs.
2. Switch `LLM_PROVIDER` to a configured fallback.
3. Keep RAG/admin read paths available even when chat generation is degraded.
