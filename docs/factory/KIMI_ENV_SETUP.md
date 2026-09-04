# Kimi credentials — which key is which

Do **not** confuse these two:

| Credential | Who uses it | Where it goes |
|------------|-------------|----------------|
| **Kimi Code CLI** | Factory Engineer / Floor C-BRIEF (`FACTORY_CODE_CLI`) | `~/.kimi-code/config.toml` under `[providers.kimi]` — **required** for CLI auth |
| **In-app chat LLM** | CerebrumDev UI / kit chat | `LLM_PROVIDER` + `OLLAMA_*` (default) or optional `CEREBRUM_LLM_*` / `KIMI_API_KEY` for Factory architect |

Kimi Code CLI does **not** read shell `export KIMI_API_KEY=...` or `backend/.env` for authentication.
See [Kimi Code config files](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files).

## Production Floor (C-BRIEF)

A keyed Factory Floor dispatches **one** compiled brief through `FACTORY_CODE_CLI`
(default name `kimi`; `KIMI_CODE_CLI` still honoured). The production image does
**not** ship that binary. If the coder is on and the executable is missing,
dispatch fail-closes as `FACTORY_CODE_CLI_UNAVAILABLE` — no HTTP oneshot, no
"coding agent has taken over" / WRITER progress that cannot deliver a real
session. `GET /health` → `factory_code_cli` reports the probe.

Exact env (owner-gated on Render; this doc does not claim the dashboard is set):

| Variable | Role |
|----------|------|
| `FACTORY_CODE_CLI` | Binary name or absolute path (`kimi` / `claude` / `/abs/path`) |
| `KIMI_CODE_CLI` | Legacy alias; `FACTORY_CODE_CLI` wins |
| `KIMI_CODE_API_KEY` | Writes `~/.kimi-code/config.toml` at boot when set |
| `KIMI_CODE_HOME` | Optional config home (Render: `/app/.kimi-code` if you pin it) |
| `FACTORY_BRIEF_HTTP_ONESHOT=1` | CI/dev escape only — **not** a ≥2h CLI session |
| `FACTORY_BRIEF_REQUIRE_CLI=1` | Force generate-start refuse (CI mutation). `ENV=production` already requires the CLI when the coder is on |

`CEREBRUM_LLM_API_KEY` / `KIMI_API_KEY` arm the architect / HTTP coder. They do
**not** install or authenticate `FACTORY_CODE_CLI`.

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
