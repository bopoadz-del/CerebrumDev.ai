# Kimi credentials — which key is which

Do **not** confuse these two:

| Credential | Who uses it | Where it goes |
|------------|-------------|----------------|
| **Kimi Code CLI** | Factory Engineer / Floor C-BRIEF (`FACTORY_CODE_CLI`) | `~/.kimi-code/config.toml` under `[providers.kimi]` **and** `default_model` — required for headless `kimi --prompt` |
| **In-app chat LLM** | CerebrumDev UI / kit chat | `LLM_PROVIDER` + `OLLAMA_*` (default) or optional `CEREBRUM_LLM_*` / `KIMI_API_KEY` for Factory architect |

Kimi Code CLI does **not** read shell `export KIMI_API_KEY=...` or `backend/.env` for authentication.
See [Kimi Code config files](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files).

## Production Floor (C-BRIEF)

A keyed Factory Floor dispatches **one** compiled brief through `FACTORY_CODE_CLI`
(default name `kimi`; `KIMI_CODE_CLI` still honoured). The production image
(`./Dockerfile`) installs the official Kimi Code CLI so `kimi` is on `PATH`.
If the coder is on and the executable is still missing, dispatch fail-closes
as `FACTORY_CODE_CLI_UNAVAILABLE`. If `kimi` is on `PATH` but
`~/.kimi-code/config.toml` is absent (`credentials_file_present=false`),
it fail-closes as `FACTORY_CODE_CLI_CREDENTIALS_MISSING`. If the CLI
exits with `No model configured` (binary + credentials file, no
`default_model`), dispatch fail-closes as `FACTORY_CODE_CLI_NO_MODEL`
(still a `FACTORY_CODE_CLI_FAILED` honesty class). None of those paths
open a WRITER session or claim "coding agent has taken over". A templated
pilot zip after a CLI skip is **not** a ≥2h CLI session. HTTP oneshot is
not a substitute. `GET /health` → `factory_code_cli` reports the probe
(`available` is the binary; credentials are a separate field). Headless
Floor cannot run `kimi` `/login` (no TTY).

Exact env (owner-gated on Render; this doc does not claim the dashboard is set):

| Variable | Role |
|----------|------|
| `FACTORY_CODE_CLI` | Binary name or absolute path (`kimi` / `claude` / `/abs/path`) |
| `KIMI_CODE_CLI` | Legacy alias; `FACTORY_CODE_CLI` wins |
| `KIMI_CODE_API_KEY` | Writes `~/.kimi-code/config.toml` at boot when set (`[providers.kimi]` + `default_model`). Missing file + keyed Floor → `FACTORY_CODE_CLI_CREDENTIALS_MISSING` |
| `KIMI_CODE_MODEL` | Default-model alias written into that file. Default `kimi-code/k3` (Kimi Code CLI 0.41 [config-files](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files) example). Override if the key's catalog differs. Owner-gated — this doc does not claim Render is set |
| `KIMI_CODE_MODEL_ID` | Optional API model id for the `[models]` table (default: last path segment of `KIMI_CODE_MODEL`, e.g. `k3`) |
| `KIMI_CODE_HOME` | Optional config home (Render: `/app/.kimi-code` if you pin it) |
| `FACTORY_BRIEF_HTTP_ONESHOT=1` | CI/dev escape only — **not** a ≥2h CLI session |
| `FACTORY_BRIEF_REQUIRE_CLI=1` | Force generate-start refuse (CI mutation). `ENV=production` already requires the CLI when the coder is on |

`CEREBRUM_LLM_API_KEY` / `KIMI_API_KEY` arm the architect / HTTP coder. They do
**not** install or authenticate `FACTORY_CODE_CLI`.

## Production image install (`./Dockerfile`)

Render `cerebrumdev-backend` is built from the repo-root `Dockerfile`. That
image installs the **official** Kimi Code CLI (standalone glibc binary; no
Node.js) via the documented installer, pinned with `KIMI_CODE_VERSION`:

| Item | Value |
|------|--------|
| Docs | https://www.kimi.com/code/docs/en/kimi-code-cli/guides/getting-started |
| Installer | https://code.kimi.com/kimi-code/install.sh |
| Pin | `ARG KIMI_CODE_VERSION=0.41.0` (`KIMI_VERSION` on the installer) |
| Install dir | `KIMI_INSTALL_DIR=/usr/local` → **`/usr/local/bin/kimi`** |
| Verify | `which kimi` and `kimi --version` inside the image |

Bump the pin by changing `KIMI_CODE_VERSION` (and rebuild). Do **not** commit
API keys; `KIMI_CODE_API_KEY` stays owner-gated on the Render dashboard and
writes `$HOME/.kimi-code/config.toml` (or `$KIMI_CODE_HOME/config.toml`) at
boot, including `default_model` so `kimi --prompt` does not require TTY
`/login`. `HOME=/app` in this image, so the default config home is
`/app/.kimi-code`. A credentials-only file from an older boot is mutated
on the next start when `default_model` is missing.

A missing or wrong `FACTORY_CODE_CLI` still fail-closes. Baking the binary
does not disable that gate.

## Kimi Code (CLI)

```bash
./scripts/setup_kimi_code_env.sh 'sk-your-kimi-code-key'
# writes ~/.kimi-code/config.toml:
#   default_model = "kimi-code/k3"   # KIMI_CODE_MODEL override
#   [providers.kimi]
#   type = "kimi"
#   api_key = "sk-..."
#   base_url = "https://api.moonshot.ai/v1"
#   [models."kimi-code/k3"]
#   provider = "kimi"
#   model = "k3"
#   max_context_size = 1048576
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
