# Implementation Plan: Automotive Safety Intelligence Pilot — PR 3

> **Plan file:** `docs/superpowers/plans/2026-07-11-automotive-pilot-pr3-pilot-release.md`  
> **Parent design spec:** `docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md`  
> **Branch:** `feat/rag-vector-index-dry-run`  
> **PR title:** `feat(pilot): automotive frontend, admin, deployment and E2E acceptance`  
> **Target repo:** `bopoadz-del/CerebrumDev.ai` (generates the pilot deployment)  
> **Estimated effort:** large

---

## Outcome

PR 3 delivers a complete, deployed **Automotive Safety Intelligence Pilot** that a user can operate through a browser:

- Automotive-branded frontend derived from The_Fork.
- Login, foundation workspace, streaming chat with citations.
- Project workspace, private document upload, and client overlay retrieval.
- Multi-user Google Drive connector for private project documents.
- Admin foundation-pack controls.
- Security and tenant-isolation tests.
- Live deployed instance with health checks and audit visibility.

No construction-specific branding or retrieval assumptions remain active. The public Automotive Core RAG and client-private overlays are visibly separated.

---

## Scope guard

**In scope**

- Frontend branding conversion (strings, navigation, quick actions, empty states, source labels).
- Admin Automotive Core pack status/controls page.
- Project-scoped private document upload and indexing.
- Google Drive folder binding and sync for private projects.
- Layer-aware retrieval blending public foundation + private project evidence.
- Citation panel with `Automotive Core` / `Private Project` labels.
- End-to-end browser tests for critical journeys.
- Security tests for JWT, ownership, non-leaking 404s, upload controls, CORS, etc.
- Generated deployment package and live pilot deployment.
- Final release report.

**Out of scope**

- New foundation corpus harvesting (PR 2).
- New embedding/indexing backend (PR 2).
- Changes to Cerebrum-Blocks, The_Fork live platform, chain generation, formula_executor_v2 internals.
- Billing, marketplace, mobile app, voice interface, fine-tuning, LoRA.

---

## Files to inspect before writing

```text
frontend/                     (from PR 1 generated platform)
frontend/src/App.tsx
frontend/src/components/Chat/
frontend/src/components/Citations/
frontend/src/components/Admin/
frontend/src/components/Projects/
frontend/src/components/GoogleDrive/
backend/app/routers/
backend/app/core/rag_retrieval.py
backend/app/core/google_drive_connector.py   (from PR 1)
backend/app/models/google_drive.py           (from PR 1)
automotive_platform_manifest.json            (from PR 1)
```

---

## Part A — Frontend branding conversion

### 1. Manifest-driven branding

The generated platform manifest (from PR 1) contains:

```json
{
  "branding": {
    "product_name": "Automotive Safety Intelligence",
    "workspace_name": "Vehicle Safety Workspace",
    "foundation_name": "Automotive Core Knowledge"
  }
}
```

Frontend must read these values at build time or from a `/config` endpoint and replace all user-visible construction strings.

### 2. Required string replacements

| Old (construction) | New (automotive) |
|--------------------|------------------|
| The Fork | Automotive Safety Intelligence |
| Construction Workspace | Vehicle Safety Workspace |
| Project | Project |
| RFI / NCR / VO / BOQ | Recall / Complaint / Investigation / Safety Rating |
| Drawing reference | Campaign / ODI / Investigation number |
| Training material | Automotive Core Knowledge |
| Construction knowledge base | Automotive Core Knowledge |

### 3. Navigation and quick actions

Update sidebar/nav labels:

```text
Vehicle Safety Workspace
Automotive Core Knowledge
My Projects
Admin
```

Quick actions on home/workspace:

```text
Check recalls by vehicle
Search campaign number
Review complaint patterns
Compare vehicle safety records
Summarize an investigation
```

### 4. Empty states and help text

Replace construction examples with automotive examples:

```text
"Ask about a recall, complaint pattern, or vehicle safety rating."
"Upload a service report, dealer bulletin, or fleet incident summary."
```

---

## Part B — Admin foundation-pack controls

### 1. Admin page

Create:

```text
frontend/src/components/Admin/AutomotiveCorePanel.tsx
```

Display:

```text
Pack version
Status
Harvest timestamp
Source families
Source counts
Document/record count
Chunk count
Embedding identity
Index namespace
Evaluation result
Last refresh
Last error
Activation status
```

Buttons:

```text
Build / Resume Pack
Verify Pack
Run Evaluation
Activate Validated Version
Rollback to Prior Version
```

Destructive rebuild requires typed confirmation modal.

### 2. Admin route guard

Reuse existing admin gating from The_Fork template. Admin-only routes must return 403 for non-admin users.

---

## Part C — Project-private document overlay

### 1. Project model

Reuse The_Fork project ownership model. Private documents belong to a project.

Document indexing target:

```text
knowledge_layer: client_private
project_id: <project_id>
```

### 2. Upload flow

Browser journey:

```text
Create Project → Upload Document → Indexing Progress → Ask Question
```

API endpoints (generated platform):

```text
POST   /projects/{project_id}/documents
GET    /projects/{project_id}/documents
GET    /projects/{project_id}/documents/{document_id}/status
DELETE /projects/{project_id}/documents/{document_id}
```

Indexing must use the same BGE-small-en-v1.5 384-dim pipeline as the foundation pack, but into the project-scoped collection.

### 3. Deletion

Deleting a private document must remove its chunks and embeddings from the private index. Foundation corpus must remain untouched.

---

## Part D — Multi-user Google Drive integration UI

### 1. Connector status

Add to project settings:

```text
Connect Google Drive → folder picker → bind folder → sync
```

### 2. Folder picker

Use Google Picker API or Drive file selector.

- Authenticate per user.
- Select one folder.
- Store `drive_connection_id` and `folder_binding_id`.
- Trigger sync job.

### 3. Sync and indexing

Reusable connector from PR 1 handles:

- List files in bound folder.
- Download supported types (PDF, DOCX, TXT, CSV).
- Create acquisitions under the project.
- Canonicalize, chunk, embed, index into `client_private` layer.

### 4. Disconnect

User can disconnect Drive. This must:

- Revoke OAuth token.
- Remove binding.
- Delete indexed documents associated with that binding.
- Leave other users’ bindings untouched.

---

## Part E — Layer-aware retrieval and citations in chat

### 1. Chat request

When a project is active, include `project_id` in the retrieval request.

```python
retrieval_request = RetrievalRequest(
    query=user_message,
    knowledge_layers=["client_private", "automotive_core_v1"],
    project_id=active_project_id,
)
```

### 2. Citation rendering

Each citation must show:

```text
[Automotive Core] Recall 20V123 — NHTSA
[Private Project] Dealer Service Report — My Project
```

Citations panel groups by knowledge layer.

### 3. Ranking behavior

- For project-specific questions: client-private evidence first, then automotive core supplement.
- For general automotive questions: automotive core evidence first; client-private only if explicitly relevant.

---

## Part F — End-to-end browser tests

Use Playwright or the existing browser test harness.

Required journeys:

```text
1. register or login
2. open foundation workspace
3. ask exact recall question
4. ask broad complaint-pattern question
5. inspect citations
6. create private project
7. upload private document
8. wait for indexing
9. ask private-document question
10. verify private citation
11. verify foundation supplement
12. delete private document
13. verify deleted evidence no longer retrieved
14. admin rebuild/resume test
15. evaluation execution
16. logout and unauthorised access test
17. second-user isolation test
```

---

## Part G — Security tests

Add:

```text
backend/tests/test_automotive_security.py
frontend/e2e/security.spec.ts
```

Prove:

- JWT enforcement on all routes.
- Admin-route enforcement.
- Project ownership enforcement.
- Non-leaking 404 behavior.
- Upload size/type controls.
- SSRF controls on source acquisition.
- Safe filename handling.
- No filesystem-path exposure.
- No raw exception leakage.
- CORS restrictions.
- Secret scanning (pre-commit or CI).
- Dependency scanning.
- SQL injection checks on query params.
- Stored-content escaping.
- Cross-user project isolation.
- Google Drive token encryption and non-exposure.

---

## Part H — Deployment

### 1. Generated package

PR 1 generator produces a deployable package. PR 3 ensures it contains:

```text
Fork-derived platform runtime
automotive domain manifest
automotive_v2 assets
formula_executor_v2
automotive assistant configuration
foundation-pack configuration
database migrations
frontend branding
deployment files
environment template
health checks
evaluation suite
```

### 2. Live pilot instance

Deploy one isolated instance with:

```text
own database
own persistent storage
own secret key
own admin account
own OAuth config
own vector namespace
own foundation corpus
```

### 3. Environment template

Add to generated package:

```text
.env.example
```

Required keys:

```text
DATABASE_URL
SECRET_KEY
JWT_ALGORITHM
JWT_EXPIRATION_MINUTES
RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RAG_EMBEDDING_DIMENSIONS=384
RAG_VECTOR_NAMESPACE=chunks_v2
PGVECTOR_DIMENSION=384
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI
ADMIN_EMAIL
```

### 4. Health checks

Ensure `/health`, `/ready`, and `/metrics` endpoints exist and pass.

---

## Part I — Verification commands

```bash
# Backend full suite
cd backend
python -m pytest tests -q

# Frontend lint and build
cd frontend
npm run lint
npm run build

# E2E browser tests (requires running app)
npm run test:e2e

# Security scan example
npx secretlint "**/*"
npm audit --audit-level=moderate
```

---

## Part J — Final release report

After merge, produce:

```text
docs/superpowers/reports/2026-07-11-automotive-pilot-release-report.md
```

Sections:

```text
KNOWN
- repositories
- source branches
- pinned Fork commit
- pinned Cerebrum-Blocks commit
- generated platform repository or artifact
- files created and changed
- active embedding identity
- active vector namespace
- harvested source families
- exact corpus counts
- exact chunk counts
- deployment URL
- exact commands run
- exact test results
- browser results
- evaluation results
- CI results
- PR numbers
- merge commits

IMPLEMENTED
- Fork-derived domain-neutral platform template
- automotive platform manifest
- automotive core RAG pack
- official data harvest
- normalized automotive record models
- semantic embeddings
- pgvector/BM25 indexing
- layer-aware hybrid retrieval
- automotive grounded assistant
- citations
- frontend conversion
- admin pack controls
- private project overlay
- Google Drive integration
- generation packaging
- deployment
- evaluation suite
- security and E2E tests

NOT CHANGED
- live construction corpus
- live construction client records
- The_Fork production secrets
- unrelated Cerebrum-Blocks domain kits
- other 16 domain RAG packs
- coding-agent system
- fine-tuning
- billing

RISKS
- corpus freshness and refresh requirements
- official-data field inconsistencies
- complaint narratives are reports, not confirmed defect findings
- source volume and indexing cost
- domain-template extraction may reveal hidden construction coupling
- embedding-model migration compatibility
- deployment resource requirements
- private/public retrieval ranking calibration
```

---

## Part K — Commit plan

Create commits in this order:

1. `feat(pilot): convert frontend branding to automotive`
2. `feat(pilot): add admin automotive core pack controls`
3. `feat(pilot): add project-private document upload and indexing`
4. `feat(pilot): add Google Drive folder binding and sync UI`
5. `feat(pilot): wire layer-aware retrieval and citations in chat`
6. `test(pilot): add E2E browser journeys`
7. `test(pilot): add security and isolation tests`
8. `chore(pilot): add deployment template and release report`

---

## Definition of done

- [ ] Frontend is automotive-branded with no construction-specific strings active.
- [ ] Admin can view and control the Automotive Core pack.
- [ ] Users can create projects and upload private documents.
- [ ] Google Drive can be connected, bound to a project, and synced.
- [ ] Chat streams answers with separated `Automotive Core` and `Private Project` citations.
- [ ] E2E critical journeys pass.
- [ ] Security tests pass.
- [ ] Deployed pilot instance is live and healthy.
- [ ] Evaluation gates meet targets or deviations are documented.
- [ ] Full backend and frontend CI passes.
