# Kimi credentials — which key is which

Do **not** confuse these two:

| Credential | Who uses it | Purpose |
|------------|-------------|---------|
| **Kimi Code CLI** (`KIMI_CODE_API_KEY`) | Kimi Code (engineer / Block Store Manager) | Coding, Store ops, shipper agent in the CLI — **not** the Factory web chat LLM |
| **In-app chat LLM** (`OLLAMA_*` today, or later a dedicated architect key) | CerebrumDev UI / kit chat | Streaming chat + chain suggestions in the browser |

## Kimi Code (CLI) — what you paste for the coder

```bash
# backend/.env (gitignored)
KIMI_CODE_API_KEY='sk-...'
KIMI_CODE_BASE_URL='https://api.moonshot.ai/v1'   # international Moonshot/Kimi Code
```

This does **not** change `LLM_PROVIDER` and does **not** switch kit chat off Ollama.

## In-app chat LLM (unchanged default)

Production kit chat stays on Ollama cloud unless you explicitly choose otherwise:

```bash
LLM_PROVIDER=ollama
OLLAMA_URL=https://ollama.com
OLLAMA_MODEL=kimi-k2.7-code:cloud
OLLAMA_API_KEY=...
```

## Role reminder

| Actor | Job |
|-------|-----|
| **Kimi API** (architect chat — separate product path) | Blueprint talk in Factory UI when wired |
| **Kimi Code** | Factory Engineer + Block Store Manager via CLI |
| **CerebrumDev.ai** | Governance, dual registry, certify, regenerate |

## Helper

```bash
# Stores KIMI_CODE_* only — does not set LLM_PROVIDER=kimi
./scripts/setup_kimi_code_env.sh 'sk-your-kimi-code-key'
```
