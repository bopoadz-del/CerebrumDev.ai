# AGENTS.md

## Cursor Cloud specific instructions

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
