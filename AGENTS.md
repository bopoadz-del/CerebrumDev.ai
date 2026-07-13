# AGENTS.md

## Cursor Cloud specific instructions

### RetailOps generated runtime (`backend/app/retailops/`)
The RetailOps platform requires **PostgreSQL 16 with the `pgvector` extension**.
It is a separate stack from the legacy configurator (which uses ChromaDB) and
lives entirely under `backend/app/retailops/`.

- Run the runtime (from `backend/`): `uvicorn app.retailops.app_factory:app`.
  It applies Alembic migrations on startup (set `RETAILOPS_SKIP_MIGRATIONS=1` to
  skip). The DB URL comes from `RETAILOPS_DATABASE_URL` (or `DATABASE_URL`).
- **Tests need a real Postgres+pgvector DB.** They read
  `RETAILOPS_TEST_DATABASE_URL` (default
  `postgresql+psycopg://retailops:retailops@localhost:5432/retailops_test`) and
  **skip** (not fail) if it is unreachable. To run them:
  `python -m pytest tests/retailops` from `backend/`.
- Creating the `vector` extension requires a role with sufficient privilege;
  the migration runs `CREATE EXTENSION IF NOT EXISTS vector`.
- Default embeddings are a deterministic feature-hash (no ML deps). Set
  `RETAILOPS_EMBED_BACKEND=fastembed` for ONNX embeddings (dimension fixed at 384;
  re-embed after switching).
- The generated business UI lives in `backend/app/retailops/frontend/` (Vite +
  React + TS); `npm run build` emits `dist/`, which the runtime serves at `/`.
- The generic action contract + Retail kit are vendored from the Cerebrum-Blocks
  repo under `backend/app/retailops/kit/`, pinned to the commit in
  `backend/app/retailops/__init__.py` (`BLOCKS_COMMIT`). Re-vendor and update that
  constant together.

The legacy configurator backend (`backend/app/main.py`) and its test suite are
unchanged and run with `python -m pytest` (excluding `tests/retailops`).
