# 🧠 CerebrumDev.ai

**The visual configurator for building and deploying custom AI agents.**

CerebrumDev.ai is a full-stack platform that lets users:

- Select a domain (e.g., Medical, Retail, Construction, Hospitality)
- Upload domain-specific documents
- Chat with an AI to design a processing chain (using 50+ universal blocks)
- Fine-tune a language model on their data (via Together AI)
- Deploy a fully working AI instance (to Render) with one click

---

## 🏗️ Architecture

The platform consists of two repos:

| Repo | Role |
| :--- | :--- |
| **[CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai)** | Configurator – frontend (React) + backend (FastAPI) |
| **[Cerebrum-Blocks](https://github.com/bopoadz-del/Cerebrum-Blocks)** | Block engine + store – generic blocks + 18 domain kits |

---

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
- **Backend**: Powered by **Together AI** – upload your Q&A pairs (≥10) and fine-tune Qwen/Llama models
- **Frontend**: Dedicated `TrainingPanel` to manage pairs, start training, and poll progress
- On success, you receive a `fine_tuned_model_id` that becomes the model for your deployed instance

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
- A [Together AI](https://api.together.ai) API key (for fine-tuning)
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

3. **Frontend**
   ```bash
   cd ../frontend
   npm install
   npm run dev
   ```

4. **Open** `http://localhost:5173`

### Environment Variables (.env)

Copy `backend/.env.example` to `backend/.env` and fill in:

```env
# Cerebrum-Blocks store
CEREBRUM_API_URL=http://localhost:8000
CEREBRUM_API_KEY=cb_dev_key

# LLM provider: Ollama (Qwen fallback is also supported)
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:120b-cloud
QWEN_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus

# Storage
STORAGE_PATH=./storage
CHROMA_PERSIST_DIR=./storage/chroma

# Fine-tuning (Together AI)
TOGETHER_API_KEY=
TOGETHER_BASE_URL=https://api.together.xyz
FINE_TUNE_BASE_MODEL=Qwen/Qwen2.5-7B-Instruct

# Auto-deploy (Render + GitHub)
RENDER_API_KEY=
RENDER_OWNER_ID=
DEPLOY_REPO=https://github.com/bopoadz-del/CerebrumDev.ai
GITHUB_TOKEN=
GITHUB_USERNAME=bopoadz-del
```

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
- `POST /v1/sessions/{id}/train` – start a Together AI fine-tune job
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
- `DEPLOY_REPO` must exist and be accessible

Without a GitHub token, the deployer falls back to returning a downloadable zip.

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
- **AI**: Ollama Cloud, Together AI (fine-tuning)
- **Deployment**: Render, Docker, GitHub API
- **Storage**: ChromaDB (vectors), local files (documents)

---

## 🤝 Contributing

We welcome contributions! Please open an issue or PR.

---

## 📄 License

Proprietary – all rights reserved.
