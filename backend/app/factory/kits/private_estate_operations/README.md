# Private Estate Operations kit (Factory-side)

Factory kit definition for **Cerebrum Steward**. Consumes dual-registered Store
blocks from Cerebrum-Blocks (clone/read only — never write to Blocks from here).

| Artifact | Path |
|----------|------|
| Golden blueprint | `blueprints/steward/steward.v1.yaml` |
| Kit manifest | `backend/app/factory/vendor_blocks_mirror/private_estate_operations_kit/manifest.json` |
| Demo fixtures | `fixtures/demo_estate.json` (this directory) |
| Factory shelf | `backend/app/factory/shelves/factory_blocks.json` |

## Dual RAG

- **Layer 1** (`sop_standards`): House Manual / SOP / global standards
- **Layer 2** (`estate_documents`): per-estate documents, separately indexed

### Live path (`/v1/rag/*`)

| Mode | Env | Persistence | Embeddings |
|------|-----|-------------|------------|
| CI / local default | unset | JSONL under `data/rag/` | `local_feature_hash_v1` |
| **Pilot** | `STEWARD_DATABASE_URL` + `STEWARD_EMBED_BACKEND=fastembed` | Postgres `postgres_jsonb_v1` | `fastembed:BAAI/bge-small-en-v1.5` |

Fail-closed flags: `STEWARD_REQUIRE_PERSISTENT_RAG=1`, `STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1`
(no silent hash/JSONL fallback). Full hybrid RRF + governed packs remain on `/v1/steward/rag/*`.

## Connectors

`cmms_stub`, `iot_stub`, `document_vault_stub`, `smart_home_placeholder` are
**honest placeholders** (`STATUS = "not_implemented"`). No live IoT credentials.

## Generate

```bash
cd backend
PYTHONPATH=. python3 -m app.factory.cli generate \
  --blueprint ../blueprints/steward/steward.v1.yaml \
  --out ../factory_outputs/Cerebrum-Steward \
  --blocks-root "$CEREBRUM_BLOCKS_ROOT"
```
