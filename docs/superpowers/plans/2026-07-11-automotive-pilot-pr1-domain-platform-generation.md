# Implementation Plan: Automotive Safety Intelligence Pilot — PR 1

> **Plan file:** `docs/superpowers/plans/2026-07-11-automotive-pilot-pr1-domain-platform-generation.md`  
> **Parent design spec:** `docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md`  
> **Branch:** `feat/rag-vector-index-dry-run`  
> **PR title:** `feat(platform): domain-neutral Fork template, automotive kit and Google Drive connector`  
> **Target repo:** `bopoadz-del/CerebrumDev.ai`  
> **Estimated effort:** large

---

## Outcome

PR 1 creates the **reusable generation machinery** for the Automotive Safety Intelligence Pilot:

- A domain-neutral platform template derived from The_Fork pinned at commit `008d5e7a53654d401ce60372212f5245a8067c76`.
- A versioned automotive platform manifest consumed by the generator.
- Cerebrum-Blocks `automotive_v2` kit hardening (prompts, schemas, source manifest, evaluation definitions).
- A reusable multi-user Google Drive connector core with two integrations:
  - CerebrumDev.ai factory integration.
  - Generated-platform integration.
- A reproducible generator that emits a deployable automotive platform package.

No live construction data, secrets or frontend UI conversion happens in this PR.

---

## Scope guard

**In scope**

- Inspect and pin The_Fork baseline.
- Create domain-neutral platform template generator in CerebrumDev.ai.
- Define automotive platform manifest schema and validation.
- Harden Cerebrum-Blocks automotive kit.
- Add reusable Google Drive connector core, models, migrations, OAuth routes, folder binding, sync jobs, audit events.
- Add generated-platform Drive module wiring and manifest feature flag.
- Add ownership/isolation tests for Drive connections.
- Add generation reproducibility test.

**Out of scope**

- NHTSA harvesting and embedding/indexing (PR 2).
- Frontend conversion and E2E browser tests (PR 3).
- Live deployment (PR 3).
- Changes to The_Fork live construction platform, Fork2, chain generation, formula_executor_v2 internals, chat/LLM provider configuration.

---

## Files to inspect before writing

```text
The_Fork/                      (pinned at 008d5e7a53654d401ce60372212f5245a8067c76)
  backend/app/main.py
  backend/app/routers/
  backend/app/models/
  backend/app/core/
  frontend/src/
  docker-compose.yml
  Dockerfile
  .env.example
  migrations/

Cerebrum-Blocks/
  automotive_v2/
  formula_executor_v2/
  block_registry/

CerebrumDev.ai/
  backend/app/
  backend/tests/
  docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md
```

---

## Part A — Pin and inspect The_Fork baseline

### 1. Record pinned baseline

Create:

```text
docs/superpowers/records/2026-07-11-fork-baseline-inspection.md
```

Capture:

```text
source commit: 008d5e7a53654d401ce60372212f5245a8067c76
active embedding model: BAAI/bge-small-en-v1.5
active embedding dimensions: 384
active vector namespace: chunks_v2
database schema summary
hybrid retrieval implementation location
authentication implementation
project ownership model
admin permissions model
audit model
frontend routes
SSE chat contract
source citation contract
deployment configuration
test and CI commands
```

### 2. Add pinned commit reference to generator config

Create:

```text
backend/app/platform_generator/fork_baseline.py
```

```python
FORK_BASELINE_COMMIT = "008d5e7a53654d401ce60372212f5245a8067c76"
```

---

## Part B — Automotive platform manifest schema

### 1. Manifest model

Create:

```text
backend/app/models/platform_manifest.py
```

```python
class AutomotivePlatformManifest(BaseModel):
    schema_version: str = "1.0.0"
    platform_id: str = "automotive-safety-intelligence"
    platform_name: str = "Automotive Safety Intelligence"
    domain: str = "automotive"
    domain_kit: str = "automotive"
    domain_block: str = "automotive_v2"
    formula_block: str = "formula_executor_v2"
    foundation_rag_pack: str = "automotive_core_rag_v1"
    foundation_collection: str = "automotive_core_v1"
    client_overlay_enabled: bool = True
    client_collection_strategy: str = "project_scoped"
    reference_identifiers: list[str] = [
        "nhtsa_campaign_number",
        "odi_number",
        "investigation_number",
        "make",
        "model",
        "model_year",
        "component",
        "manufacturer",
    ]
    branding: BrandingConfig
    features: FeaturesConfig
```

### 2. Validation

Add:

```python
def validate_manifest(manifest: AutomotivePlatformManifest) -> None: ...
```

Fail fast on:

```text
missing required blocks
unknown domain kit
unrecognized reference identifiers
invalid collection strategy
missing branding fields
missing feature flags
```

### 3. Example manifest file

Create:

```text
docs/superpowers/fixtures/automotive_platform_manifest.json
```

---

## Part C — Domain-neutral platform template generator

### 1. Generator module

Create:

```text
backend/app/platform_generator/__init__.py
backend/app/platform_generator/generator.py
backend/app/platform_generator/template_filters.py
backend/app/platform_generator/renderers.py
backend/scripts/generate_automotive_platform.py
```

### 2. Template rules

The generator copies The_Fork baseline and applies transformations:

- Replace construction branding strings using manifest `branding`.
- Replace construction reference identifiers with automotive identifiers.
- Remove construction-only quick actions and labels.
- Keep authentication, project ownership, admin gating, audit, SSE chat, citations, document upload.
- Keep Postgres, pgvector, BM25, hybrid retrieval, health checks, metrics.
- Keep deployment files but rename service names to automotive pilot.

### 3. Parameterized strings

Create a mapping file:

```text
backend/app/platform_generator/construction_to_automotive.json
```

Example entries:

```json
{
  "The Fork": "{{ product_name }}",
  "Construction Workspace": "{{ workspace_name }}",
  "training_material": "automotive_core_knowledge",
  "RFI": "recall",
  "NCR": "complaint",
  "VO": "investigation",
  "BOQ": "safety_rating"
}
```

### 4. Reproducibility

Generation must be deterministic given:

```text
pinned Fork commit
pinned Cerebrum-Blocks commit
platform manifest
```

Add a hash of inputs to generated output:

```text
generated/automotive-safety-intelligence/.generation_inputs_hash
```

---

## Part D — Cerebrum-Blocks automotive kit hardening

### 1. Files to add/update in Cerebrum-Blocks

```text
automotive_v2/prompts/assistant_v1.txt
automotive_v2/schemas/recall.json
automotive_v2/schemas/complaint.json
automotive_v2/schemas/investigation.json
automotive_v2/schemas/safety_rating.json
automotive_v2/schemas/vehicle_identity.json
automotive_v2/source_manifest.json
automotive_v2/evaluation/golden_questions.jsonl
automotive_v2/README.md
automotive_v2/manifest.json
```

### 2. Block manifest

Update `automotive_v2/manifest.json`:

```json
{
  "block_id": "automotive_v2",
  "block_version": "2.0.0",
  "domain": "automotive",
  "capabilities": ["rag", "retrieval", "chat", "formula_executor"],
  "depends_on": ["formula_executor_v2"],
  "prompts": ["prompts/assistant_v1.txt"],
  "schemas": ["schemas/"],
  "rag_pack": "automotive_core_rag_v1"
}
```

### 3. Source manifest

Place the authoritative source manifest in Cerebrum-Blocks:

```text
automotive_v2/source_manifest.json
```

It defines the NHTSA source families, authority, jurisdiction, and refresh policy. The CerebrumDev.ai harvester consumes this manifest.

### 4. Keep corpus out of Git

Ensure `.gitignore` in Cerebrum-Blocks excludes:

```text
*.csv
*.zip
*.jsonl
harvests/
embeddings/
indexes/
```

Only schemas, manifests, prompts, evaluation definitions, and small fixtures are source-controlled.

---

## Part E — Reusable Google Drive connector core

### 1. Data models

Create:

```text
backend/app/models/google_drive.py
```

Entities:

```python
class Organisation(BaseModel): ...
class Tenant(BaseModel): ...
class User(BaseModel): ...
class Membership(BaseModel): ...
class DriveConnection(BaseModel):
    connection_id: str
    owner_type: Literal["organisation", "tenant", "user"]
    owner_id: str
    user_id: str
    provider: Literal["google_drive"]
    encrypted_refresh_token: str
    scope: str
    created_at: datetime
    updated_at: datetime

class DriveFolderBinding(BaseModel):
    binding_id: str
    connection_id: str
    bound_object_type: Literal["factory_workspace", "project"]
    bound_object_id: str
    folder_id: str
    folder_name: str
    created_at: datetime

class DriveSyncJob(BaseModel): ...
class DriveFileRecord(BaseModel): ...
class IndexedDocument(BaseModel): ...
class AuditEvent(BaseModel): ...
```

### 2. Connector service

Create:

```text
backend/app/core/google_drive_connector.py
```

Responsibilities:

- OAuth flow initiation with state validation and PKCE.
- Token exchange and encrypted refresh-token storage.
- Folder listing and folder picker data.
- File listing within bound folder.
- File download for supported types.
- Sync job scheduling and state tracking.
- Disconnect and revocation.
- Audit logging.

### 3. Encryption

Use a configured encryption key:

```text
DRIVE_TOKEN_ENCRYPTION_KEY
```

Never log tokens or return them in API responses.

### 4. Factory integration

Add routes:

```text
GET  /factory/google-drive/auth
GET  /factory/google-drive/callback
POST /factory/google-drive/disconnect
GET  /factory/google-drive/folders
POST /factory/google-drive/bind
```

Ownership scope:

```text
organisation_id
user_id
factory_workspace_id
drive_connection_id
folder_binding_id
```

### 5. Generated-platform integration

Add module that is included in generated packages:

```text
backend/app/platform_generator/templates/google_drive_module/
```

Routes:

```text
GET  /projects/{project_id}/google-drive/auth
GET  /projects/{project_id}/google-drive/callback
POST /projects/{project_id}/google-drive/disconnect
GET  /projects/{project_id}/google-drive/folders
POST /projects/{project_id}/google-drive/bind
GET  /projects/{project_id}/google-drive/sync-status
```

Ownership scope:

```text
tenant_id
user_id
project_id
drive_connection_id
folder_binding_id
```

### 6. Feature flag

Manifest:

```json
{
  "features": {
    "google_drive": {
      "enabled": true,
      "multi_user": true,
      "personal_connections": true,
      "organisation_shared_bindings": true,
      "default_scope": "read_only"
    }
  }
}
```

---

## Part F — Database migrations

Add migrations for new tables:

```text
drive_connection
drive_folder_binding
drive_sync_job
drive_file_record
indexed_document (for Drive-imported docs)
audit_event
```

Include both CerebrumDev.ai factory schema and generated-platform schema. The generator must emit the same migrations for the automotive pilot.

---

## Part G — Tests

Add:

```text
backend/tests/test_platform_manifest.py
backend/tests/test_platform_generator.py
backend/tests/test_google_drive_connector.py
backend/tests/test_google_drive_factory_routes.py
backend/tests/test_google_drive_generated_routes.py
backend/tests/test_google_drive_isolation.py
```

Prove:

- Manifest validates required fields.
- Invalid manifest fails with clear errors.
- Generator produces deterministic output from same inputs.
- Generated package does not contain construction secrets or live data.
- Generated package contains automotive manifest and Drive module.
- Drive OAuth state validation rejects mismatched state.
- Tokens are encrypted and never exposed in API responses.
- Cross-organisation/cross-tenant access is blocked.
- Disconnect removes binding and indexed documents.
- One user’s disconnect does not affect another user.

---

## Part H — Verification commands

```bash
cd backend

python -m py_compile \
  app/models/platform_manifest.py \
  app/models/google_drive.py \
  app/platform_generator/generator.py \
  app/core/google_drive_connector.py \
  app/routers/factory_google_drive.py \
  app/routers/project_google_drive.py

python -m pytest \
  tests/test_platform_manifest.py \
  tests/test_platform_generator.py \
  tests/test_google_drive_connector.py \
  tests/test_google_drive_factory_routes.py \
  tests/test_google_drive_generated_routes.py \
  tests/test_google_drive_isolation.py \
  -q --tb=short

python -m pytest tests -q
```

---

## Part I — Commit plan

Create commits in this order:

1. `feat(platform): pin and inspect The_Fork baseline`
2. `feat(platform): add automotive platform manifest schema and validation`
3. `feat(platform): add domain-neutral platform generator`
4. `feat(automotive): harden Cerebrum-Blocks automotive_v2 kit`
5. `feat(drive): add reusable Google Drive connector core and models`
6. `feat(drive): add CerebrumDev.ai factory Google Drive routes`
7. `feat(drive): add generated-platform Google Drive module and feature flag`
8. `test(platform): add manifest, generator, and Drive isolation tests`

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Hidden construction coupling in template | Generate and diff against baseline; inspect for remaining construction identifiers. |
| Token encryption key mishandling | Fail startup if key missing; use standard cryptography; never log tokens. |
| OAuth redirect URI mismatch | Separate apps/configs for factory vs generated platforms; validate redirect URI. |
| Cerebrum-Blocks repo bloat | Strict `.gitignore`; reject large data files in PR review. |

---

## Definition of done

- [ ] The_Fork baseline is pinned and inspected.
- [ ] Automotive platform manifest schema exists and validates.
- [ ] Domain-neutral generator produces reproducible automotive platform package.
- [ ] Cerebrum-Blocks automotive kit contains schemas, prompts, source manifest, evaluation definitions.
- [ ] Google Drive connector core supports factory and generated-platform integrations.
- [ ] Migrations cover all Drive entities.
- [ ] Feature flag controls Drive availability.
- [ ] New tests pass and full backend suite passes.
- [ ] CI passes.
