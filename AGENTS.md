# AGENTS.md

## Cursor Cloud specific instructions

### Committed cloud environment (versioned)

- Cloud agents use [`.cursor/environment.json`](.cursor/environment.json) (Dockerfile build + idempotent `install` + multitasking `terminals` for backend/frontend).
- Base image: [`.cursor/Dockerfile`](.cursor/Dockerfile) (Ubuntu 24.04, Python 3 + Node 20 + build tools). Do not `COPY` the project into that image.
- To reuse across other repos: copy `docs/cursor-cloud/reusable-base/` into that repo's `.cursor/` and edit only `install` / `terminals`. See [`docs/cursor-cloud/REUSE.md`](docs/cursor-cloud/REUSE.md).

### Local dev environment (non-obvious)

- Backend deps live in a venv at `backend/venv` (Python 3.12 on the VM works; CI/Docker pin 3.11). Run the API in dev with `cd backend && ENV=dev ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`. Health/readiness: `GET /health`, `GET /ready`.
- Use port **8000** for the backend in dev (not 8001). Vite proxies `/v1` to `http://localhost:8000` (`frontend/vite.config.ts`); the `8001` in README/`docker-compose.yml` is only the Docker port mapping.
- Frontend: `cd frontend && npm run dev -- --host` (Vite on 5173). The app auto-creates a session on load. Lint/build/test commands are in `frontend/package.json` and `.github/workflows/ci.yml`.
- The Cerebrum-Blocks domain store is an **optional external service** (`CEREBRUM_API_URL`). Without it, `/v1/domains/*` returns 503 and the UI shows a "Domain store unreachable" toast — this is expected in dev. The Factory "Design product" flow (`/v1/factory/product/*`, `/v1/sessions/{id}/product/draft`) works fully offline from golden blueprints in `blueprints/`.
- Backend tests: `cd backend && ENV=test ./venv/bin/python -m pytest` (see `pytest.ini`). Factory subset: `./venv/bin/python -m pytest tests/factory`.

### PR babysit / auto-merge (owner rule)

For Resident Engineer and related Factory milestone PRs on this repo:

1. Babysit CI and triage review comments until the PR is merge-ready.
2. When **all required checks are green**, the PR is **MERGEABLE/CLEAN**, and
   blocking comments are addressed (or validly declined), **merge immediately** —
   do **not** wait for an extra human review round.
3. Never force-push. Never weaken CI workflows just to pass.

See also `docs/resident-engineer/STANDING_ORDERS.md`.

### CerebrumDev.ai is the Factory

This repository is the **product-generation factory**. It is not a RetailOps or
Steward runtime host.

- Configurator / legacy stack: `backend/app/main.py` (ChromaDB sessions, packager).
- Product-kernel + blueprint generation live under `backend/app/factory/` (see
  Factory-first Steward plan).
- Generated products are separate repositories (e.g.
  `bopoadz-del/TEKsystems_GlobalRetailMNC`, future `Cerebrum-Steward`).

### Provenance: TEKsystems was Factory-driven

Do **not** reintroduce a runnable `backend/app/retailops/` tree here.

Proof that TEKsystems / RetailOps was driven through this Factory is retained in:

- [`docs/provenance/teksystems-retailops/FACTORY_DRIVEN_PROOF.md`](docs/provenance/teksystems-retailops/FACTORY_DRIVEN_PROOF.md)

Live RetailOps kernel: https://github.com/bopoadz-del/TEKsystems_GlobalRetailMNC

### Tests

Legacy configurator: `python -m pytest` from `backend/` (no `tests/retailops`).

Factory / product-generation tests: `python -m pytest tests/factory` from
`backend/` when present.

### Blocks dual-registration

Every block used to generate a product must be registered in **both**:

1. Cerebrum-Blocks (`block_registry/` + kit shelf)
2. CerebrumDev.ai Factory shelf/registry (`backend/app/factory/block_registry/` or
   equivalent shelf consumer)


## Product factory (Milestone 1+)

- Kernel: `backend/app/cerebrum_product_kernel/`
- Factory: `backend/app/factory/` (`blueprint`, `planner`, `dual_registry`, `generator`)
- Blueprints: `blueprints/`
- Factory block shelf (dual-register mirror): `backend/app/factory/shelves/factory_blocks.json`
- Generate: `cd backend && PYTHONPATH=. python3 -m app.factory.cli generate --blueprint ../blueprints/steward/steward.v1.yaml --out ../factory_outputs/Cerebrum-Steward --blocks-root $CEREBRUM_BLOCKS_ROOT`
- Factory tests: `python3 -m pytest tests/factory --asyncio-mode=auto`


### Product architect API

- `POST /v1/factory/product/draft` — brief → `product_blueprint.v1` (Steward brief uses golden YAML)
- `POST /v1/factory/product/plan` — plan capabilities (fail-closed dual registry)
- `POST /v1/factory/product/generate` — regenerate product into `factory_outputs/`
