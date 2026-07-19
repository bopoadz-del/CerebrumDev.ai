# Kimi credentials — which key is which

Do **not** confuse these two:

| Credential | Who uses it | Where it goes |
|------------|-------------|----------------|
| **Kimi Code CLI** | Factory Engineer / Store Manager (CLI) | `~/.kimi-code/config.toml` under `[providers.kimi]` — **required** for CLI auth |
| **In-app chat LLM** | CerebrumDev UI / kit chat | `LLM_PROVIDER` + `OLLAMA_*` (default) or optional `CEREBRUM_LLM_*` / `KIMI_API_KEY` for Factory architect |

Kimi Code CLI does **not** read shell `export KIMI_API_KEY=...` or `backend/.env` for authentication.
See [Kimi Code config files](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files).

## Kimi Code (CLI)

```bash
./scripts/setup_kimi_code_env.sh 'sk-your-kimi-code-key'
# writes ~/.kimi-code/config.toml:
#   [providers.kimi]
#   type = "kimi"
#   api_key = "sk-..."
#   base_url = "https://api.moonshot.ai/v1"
```

Override home with `KIMI_CODE_HOME` if needed. The helper also notes the key in
`backend/.env` as `KIMI_CODE_*` for humans — that does **not** authenticate the CLI
and does **not** change `LLM_PROVIDER` or strip in-app Kimi/Moonshot vars.

## In-app chat LLM (unchanged default)

```bash
LLM_PROVIDER=ollama
OLLAMA_URL=https://ollama.com
OLLAMA_MODEL=kimi-k2.7-code:cloud
OLLAMA_API_KEY=...
```

## Role reminder

| Actor | Job |
|-------|-----|
| **Kimi API** (architect chat) | Blueprint talk in Factory UI when wired |
| **Kimi Code** | Factory Engineer + Block Store Manager via CLI |
| **CerebrumDev.ai** | Governance, dual registry, certify, regenerate |
