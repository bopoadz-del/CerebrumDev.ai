# 🧠 CerebrumDev.ai

**Build your own specialized domain AI platform, customized to your enterprise, in days — not 6 to 12 months.**

---

## Why This Platform Exists

Enterprises want AI that understands **their** business: medical compliance, retail inventory, construction safety, hospitality operations. But building a custom AI agent today means:

- Hiring a team of ML engineers
- Managing vector databases and RAG pipelines
- Fine-tuning models (LoRAs) without breaking the bank
- Deploying and scaling infrastructure

That process takes **6 to 12 months** — and costs millions.

**CerebrumDev.ai collapses that timeline to days.** It is a visual configurator that lets domain experts (not just engineers):

1. **Select** a pre-built domain kit (18 available: Medical, Retail, Construction, etc.)
2. **Upload** their proprietary documents (PDFs, spreadsheets, manuals)
3. **Chat** with AI to design a custom processing chain (using 50+ reusable blocks)
4. **Fine-tune** the model on their data via **Tinker** (LoRA, at near-zero cost)
5. **Deploy** a production-ready, isolated AI instance to the cloud (Render) with one click

No months of infrastructure work. No ML engineering team required. Just your data, your rules, and a live API endpoint serving your customized AI.

---

## 🏗️ Architecture

The platform consists of two repos:

| Repo | Role |
| :--- | :--- |
| **[CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai)** | Configurator – frontend (React) + backend (FastAPI) |
| **[Cerebrum-Blocks](https://github.com/bopoadz-del/Cerebrum-Blocks)** | Block engine + store – generic blocks + 18 domain kits |

---



## Factory provenance

CerebrumDev.ai is the **product factory**. The live RetailOps kernel lives in
[`TEKsystems_GlobalRetailMNC`](https://github.com/bopoadz-del/TEKsystems_GlobalRetailMNC).
Evidence that RetailOps was Factory-driven is retained under
[`docs/provenance/teksystems-retailops/`](docs/provenance/teksystems-retailops/).
There is no runnable `backend/app/retailops/` package in this repository.

## ✨ Features

### Phase 1 – Configure
- Select a domain from 18 available kits (Medical, Retail, Construction, etc.)
- AI-powered session management

### Phase 2 – Upload & Index
- Drag-and-drop document upload (PDF, DOCX, TXT, images)
- Persistent vector storage per session (ChromaDB)
- All uploaded documents are indexed and available for retrieval

### Phase 3 – AI Chat & Chain Generation
- Chat with the AI (powered by Ollama Cloud, model: `gpt-oss:120b-cloud`)
- The AI proposes a block chain based on your documents and domain
- Approve, modify, and inject custom rules
- The approved chain is saved for deployment

### Phase 4 – Tinker (Fine-Tuning)
- **Backend**: Powered by **Tinker** – upload your Q&A pairs (≥10) and fine-tune Qwen/Llama models
- **Frontend**: Dedicated `TrainingPanel` to manage pairs, start training, and poll progress
- On success, the resulting `fine_tuned_model_id` (a `tinker://` path) is used for inference in the deployed instance

### Phase 5 – Ship (Deploy)
- **Packager** generates a deployable package:
  - Domain container
  - Approved chain (`default_chain.json`)
  - Vectors (`vectors.json`)
  - Uploaded documents (`data/docs/`)
  - `Dockerfile`, `render.yaml`, `.env`, bootstrap scripts
- **Deployer** pushes to a GitHub branch and calls the Render API to create a live service
- Fallback: download the zip for manual deployment
- **Live instances** are reachable with their own `/health`, `/v1/deployed/chain`, and `/v1/deployed/chat` endpoints

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Node.js 18+
- Docker (optional, for local container testing)
- A [Tinker](https://tinker-console.thinkingmachineslabinc.com) API key (for fine-tuning)
- A [Render](https://render.com) account (for deployment)

### Local Development

1. **Clone the repo**
   ```bash
   git clone https://github.com/bopoadz-del/CerebrumDev.ai.git
   cd CerebrumDev.ai
   ```

2. **Backend**
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate  # or `venv\Scripts\activate` on Windows
   pip install -r requirements.txt
   cp .env.example .env
   # Edit .env with your keys (see below)
   uvicorn app.main:app --reload
   ```

   The base `requirements.txt` now includes `fastembed`, a lightweight ONNX
   sentence embedder (~67 MB, CPU-only, no torch). This means RAG works out of
   the box on a bare install; you do not need the heavier extras unless you want
   models not available via fastembed or higher-quality local PDF parsing.

3. **Frontend**
   ```bash
   cd ../frontend
   npm install
   npm run dev
   ```

4. **CLI (optional developer install)**
   ```bash
   pip install "cerebrum-cli @ git+https://github.com/bopoadz-del/Cerebrum-Blocks#subdirectory=cli"
   ```

5. **Open** `http://localhost:5173`

### Environment Variables (.env)

Copy `backend/.env.example` to `backend/.env` and paste values from your vault
(Bitwarden/1Password). No real keys are committed in this repo.

```env
# Cerebrum-Blocks store
CEREBRUM_API_URL=http://localhost:8000
CEREBRUM_API_KEY=

# Configurator backend auth (required in production)
CEREBRUM_DEV_API_KEY=

# LLM provider: Ollama (Qwen fallback is also supported)
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:120b-cloud
QWEN_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus

# Storage
STORAGE_PATH=./storage
CHROMA_PERSIST_DIR=./storage/chroma

# Document parsing (local-only; disabled by default on Render)
MARKER_ENABLED=false  # set true only if marker-pdf is installed locally

# Fine-tuning (Tinker)
TINKER_API_KEY=
TINKER_BASE_MODEL=Qwen/Qwen2.5-7B-Instruct
TINKER_LORA_RANK=16
TINKER_BATCH_SIZE=4
TINKER_MAX_STEPS=10

# Tinker grounded adapter (inference)
GROUNDED_ADAPTER_ENABLED=false
GROUNDED_ADAPTER_REWRITE_PASS=false
GROUNDED_ADAPTER_TIMEOUT=60

# Resident Engineer / change-request loop (default OFF — see docs/resident-engineer/)
RESIDENT_ENGINEER_ENABLED=false
CHANGE_REQUEST_INTAKE_ENABLED=false
RESIDENT_EMIT_CHANGE_REQUESTS=false
# REDIS_URL=                    # optional multi-worker / queue index
# PRODUCT_CHANGE_REQUEST_SIGNING_KEY=  # optional Ed25519 private key (base64)

# Note: fine-tuned models are served via Tinker Cloud (internet required).
# Base Ollama inference works offline, but a deployed LoRA needs TINKER_API_KEY.

# Auto-deploy (Render + GitHub)
RENDER_API_KEY=
RENDER_OWNER_ID=
# Dedicated deploy target repo — must NOT be CerebrumDev.ai itself.
DEPLOY_REPO_URL=https://github.com/bopoadz-del/your-deploy-target
GITHUB_TOKEN=
GITHUB_USERNAME=bopoadz-del
```

---

### Local-only Marker PDF parsing (optional)

The configurator can use [`marker-pdf`](https://github.com/VikParuchuri/marker) for higher-quality PDF → Markdown extraction, but it is **disabled by default** and is **not installed in production images**. Marker pulls `torch`, `transformers`, and ~1.35 GB of model weights, so it is intended only for local development machines with sufficient disk and RAM.

To enable it locally:

```bash
cd backend
pip install -r requirements-marker.txt
# edit .env
MARKER_ENABLED=true
```

If Marker is not installed or `MARKER_ENABLED=false`, the upload pipeline falls back to the Cerebrum-Blocks PDF block and then to `pypdf`.

### Feature flags (default OFF)

| Flag | Default | What it gates |
|------|---------|----------------|
| `MARKER_ENABLED` | `false` | Local Marker PDF parsing |
| `GROUNDED_ADAPTER_ENABLED` | `false` | Tinker grounded adapter |
| `RESIDENT_ENGINEER_ENABLED` | `false` | Resident Mode observe/heal/diagnose APIs (M2) |
| `CHANGE_REQUEST_INTAKE_ENABLED` | `false` | Factory change-request intake + dry-run queue (M3) |
| `RESIDENT_EMIT_CHANGE_REQUESTS` | `false` | Resident L2 escalations → signed REPAIR requests (M3) |
| `REDIS_URL` | unset | Optional Redis/Key Value (health probe + queue index) |

See `docs/resident-engineer/` for Resident Engineer milestones.

### Optional heavier embedding / parsing extras

If you need models that are not in the fastembed ONNX hub, or you want both
heavier embeddings and Marker PDF parsing together, install the extras group:

```bash
cd backend
pip install -r requirements-embeddings-full.txt
```

This file is **not** part of the default install and is not included in deployed
images, keeping production builds small.

---

## 🧪 Testing the End-to-End Flow

1. Create a session and pick a domain
2. Upload a few relevant documents
3. Chat with the AI to generate a chain
4. Go to the Training panel, add 10+ Q&A pairs, and start fine-tuning
5. Wait for the job to complete (the UI polls automatically)
6. Click **Deploy** – you'll get a live Render URL
7. Visit the URL and test `/v1/deployed/chat` with a domain query

---

## 🌐 API Overview

Backend runs on `http://localhost:8001` (Cerebrum-Blocks store runs on `8000`).

- `POST /v1/sessions` – create session
- `GET /v1/sessions/{id}` – get session state
- `POST /v1/sessions/{id}/config` – save config
- `GET /v1/domains/` – list available domain kits from the store
- `POST /v1/sessions/{id}/upload` – upload documents
- `GET /v1/sessions/{id}/upload/status` – indexing progress
- `POST /v1/sessions/{id}/chat` – chat with AI (SSE)
- `POST /v1/sessions/{id}/train/data` – save Q&A training data
- `POST /v1/sessions/{id}/train` – start a Tinker fine-tune job
- `GET /v1/sessions/{id}/train/status` – poll fine-tune status
- `DELETE /v1/sessions/{id}/train` – cancel the fine-tune job
- `POST /v1/sessions/{id}/deploy?target=cloud` – package and deploy to Render
- `GET /v1/sessions/{id}/deploy/status` – deployment progress
- `GET /v1/sessions/{id}/deploy/package` – download the deployable zip

---

## 🚀 Deployment (Render)

The backend and frontend are deployed on Render.  
A new instance (deployed from a session) is created as a separate web service with its own environment and API key.

### Auto-Deploy Prerequisites
- `RENDER_API_KEY`, `RENDER_OWNER_ID` set in the parent backend
- `GITHUB_TOKEN` set for branch creation
- `DEPLOY_REPO_URL` must exist and be accessible (never point at CerebrumDev.ai itself)

Without a GitHub token, the deployer falls back to returning a downloadable zip.

### Session State Persistence

Session progress (domain, chain, training job, deployment status) is now saved to `storage/sessions/{id}/state.json` using atomic writes with a `.bak` fallback. On Render, the disk mount at `/app/storage` ensures this state survives redeploys. If `STORAGE_PATH` is not on a persistent disk, sessions will be lost on restart.

### ChromaDB Persistence on Render

The Render blueprint mounts a disk at `/app/storage`. ChromaDB writes its SQLite index to `/app/storage/chroma`, so vectors survive redeploys. Increase the disk size in production as needed.

---

## 🧩 Block Store Integration

The platform fetches available blocks and domain kits from the **Cerebrum-Blocks** engine:

- **Live**: `https://cerebrum-blocks.onrender.com/v1/blocks`
- Kits are packaged under `block_store/kits/{domain}/`
- Each kit includes a container, v2 blocks, domain rules, and types

---

## 🛠️ Technologies Used

- **Frontend**: React, TypeScript, Tailwind CSS, Lucide Icons
- **Backend**: FastAPI, Pydantic, ChromaDB, httpx
- **AI**: Ollama Cloud, Tinker (fine-tuning)
- **Deployment**: Render, Docker, GitHub API
- **Storage**: ChromaDB (vectors), local files (documents)

---

## 🤝 Contributing

We welcome contributions! Please open an issue or PR.

---

## 📄 License

Proprietary – all rights reserved.
