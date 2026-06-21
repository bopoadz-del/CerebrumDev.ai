# CerebrumDev.ai

Visual configurator for Cerebrum Blocks.

## Development

```bash
# Clone
git clone https://github.com/your-org/CerebrumDev.ai
cd CerebrumDev.ai

# Run with Docker Compose
docker-compose up --build
```

Open http://localhost:5173

## Phase 1: Configure

- Pick a domain from the Cerebrum-Blocks store
- Configure AI parameters
- Save configuration

## Phase 2: Upload & Index

- Drag and drop documents
- Background pipeline parses, chunks, embeds, and indexes them
- Indexed data is persisted in ChromaDB under `CHROMA_PERSIST_DIR`
- Each session gets its own Chroma collection (`session_{session_id}`)

## Phase 3: AI Chat + Chain Generation

- Describe your workflow in natural language
- AI proposes an orchestrator chain of Cerebrum Blocks
- Add custom business rules
- Approve the chain to move to Phase 4

## API

Backend runs on http://localhost:8001 (Cerebrum-Blocks store runs on 8000)

- `POST /v1/sessions` – create session
- `GET /v1/sessions/{id}` – get session state
- `POST /v1/sessions/{id}/config` – save config
- `GET /v1/domains/` – list available domain kits from the store
- `POST /v1/sessions/{id}/upload` – upload documents
- `GET /v1/sessions/{id}/upload/status` – indexing progress
- `POST /v1/sessions/{id}/chat` – chat with AI (SSE)
- `GET /v1/sessions/{id}/chain/preview` – preview proposed chain
- `POST /v1/sessions/{id}/chain/approve` – approve chain and inject rules

## Environment Variables

Copy `backend/.env.example` to `backend/.env` and fill in:

```env
CEREBRUM_API_URL=http://localhost:8000
CEREBRUM_API_KEY=cb_dev_key
QWEN_API_KEY=                  # DashScope API key; chain generator falls back to mock if empty
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
STORAGE_PATH=./storage
CHROMA_PERSIST_DIR=./storage/chroma
```

### ChromaDB persistence on Render

The Render blueprint mounts a disk at `/app/storage`. ChromaDB writes its SQLite index to `/app/storage/chroma`, so vectors survive redeploys. For production, increase the disk size as needed.

## License

MIT
