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

## Phase 1

- Select features (blocks)
- Pick a domain (construction, medical, finance, hotel, legal)
- Configure AI parameters
- Save configuration

## API

Backend runs on http://localhost:8000

- `POST /v1/sessions` – create session
- `GET /v1/sessions/{id}` – get session state
- `POST /v1/sessions/{id}/config` – save config

## Next Phases

- Phase 2: Upload documents
- Phase 3: Add custom rules
- Phase 4: Generate orchestrator chain
- Phase 5: Train model
- Phase 6: Deploy instance

## License

MIT
