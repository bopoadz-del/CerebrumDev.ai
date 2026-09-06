# DeepSeek V4 Pro — Factory coding CLI

Do **not** confuse these two:

| Credential | Who uses it | Where it goes |
|------------|-------------|----------------|
| **DeepSeek → Claude Code** | Factory Engineer / Floor C-BRIEF (`FACTORY_CODE_CLI=claude`) | Claude Code **subprocess** env (`ANTHROPIC_AUTH_TOKEN` + Anthropic-compat base URL). Never git. |
| **Floor / architect chat** | CerebrumDev UI / kit chat | OpenRouter free (or `CEREBRUM_LLM_*` / `KIMI_API_KEY`). **Do not** point chat at DeepSeek. |

`DEEPSEEK_API_KEY` does **not** authenticate in-app chat. Boot does **not** export
`ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` process-wide — those names would leak
into `LLM_PROVIDER=claude` and burn DeepSeek on Floor talk.

Official integration:
https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/

## Production Floor (C-BRIEF)

A keyed Factory Floor dispatches **one** compiled brief through `FACTORY_CODE_CLI`.
When `DEEPSEEK_API_KEY` is set (or `FACTORY_CODE_PROVIDER=deepseek`), the default
binary is `claude` and the session uses DeepSeek V4 Pro
(`deepseek-v4-pro` — the Anthropic-compat / OpenAI catalog id).

When that CLI is ready (binary + DeepSeek key), C-BRIEF **must** dispatch via
the Claude subprocess — including store-complete inventories that are 100%
REUSE/COMPOSE (no GENERATE gaps). Empty `inventory_gaps` is not a skip
(sess_9d0b43c81b2b4620 thin SUCCESS in ~13s with `stub_rate=1.0`). Leftover
`FACTORY_BRIEF_REQUIRE_CLI=0` / `FACTORY_BRIEF_DISPATCH=0`,
COLLECTOR/GENERATE factory-LLM fallthrough, and leftover walls ≤600s
(sess_b9fbae7 ~47s `FAILED_BUDGET_SPENT` on OpenRouter `minimax-m3:free`)
must not send WRITER through in-process OpenRouter. Budget inspect /
pilot_open must not SUCCESS thin templates (`written=0`, `stub_rate≈1.0`)
before a real stage-1 wall (≥1800s). HTTP oneshot stays CI-only.

The production image (`./Dockerfile`) installs official Claude Code at
`/usr/local/bin/claude` (pin `CLAUDE_CODE_VERSION`) **and** still installs
Kimi Code at `/usr/local/bin/kimi`.

Fail-closed (same named classes as Kimi; no HTTP oneshot takeover):

| Condition | Blocker |
|-----------|---------|
| `claude` not on `PATH` while DeepSeek is the selected coder | `FACTORY_CODE_CLI_UNAVAILABLE` |
| DeepSeek selected, `DEEPSEEK_API_KEY` unset | `FACTORY_CODE_CLI_CREDENTIALS_MISSING` |
| 404 / permission denied on the model | `FACTORY_CODE_CLI_MODEL_DENIED` |
| `[claude-code:unrecognized_model]` (or similar SDK reject) | `FACTORY_CODE_CLI_MODEL_DENIED` |
| 429 / insufficient balance / quota | `FACTORY_CODE_CLI_BILLING` |

None of those paths claim a ≥2h CLI session or founding-customer-ready /
`pilot_zip`. `FACTORY_BRIEF_HTTP_ONESHOT=1` is CI/dev only.

`GET /health` → `factory_code_cli` reports `provider`, `deepseek_key_present`
(boolean only), `default_model`, and the named `blocker`.

Keep Kimi: set `FACTORY_CODE_CLI=kimi` and `KIMI_CODE_API_KEY` (see
[KIMI_ENV_SETUP.md](KIMI_ENV_SETUP.md)). An explicit Kimi CLI name wins even
when `DEEPSEEK_API_KEY` is also present.

## Render dashboard (source of truth; not in git)

Set these after merge. This doc does **not** claim the dashboard is already set.

| Variable | Required | Role |
|----------|----------|------|
| `DEEPSEEK_API_KEY` | yes (DeepSeek path) | DeepSeek Platform key. Boot/dispatch injects it as `ANTHROPIC_AUTH_TOKEN` on the Claude Code subprocess only |
| `FACTORY_CODE_CLI` | recommended | `claude` (DeepSeek / Anthropic Claude Code) or `kimi`. When unset, `DEEPSEEK_API_KEY` defaults the binary to `claude` |
| `FACTORY_CODE_PROVIDER` | optional | `deepseek` forces the DeepSeek coder even before the key is present (fail-closed `CREDENTIALS_MISSING`) |
| `ANTHROPIC_MODEL` | optional | Override; default `deepseek-v4-pro` (catalog id). **Do not** set `deepseek-v4-pro[1m]` — Claude Code 2.1.x rejects the `[1m]` suffix as `unrecognized_model` (sess_be217f6d) |
| `DEEPSEEK_CODE_MODEL` | optional | Same override if you do not want `ANTHROPIC_MODEL` on the service (chat leak risk). Use `deepseek-v4-pro` |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | optional | Default `deepseek-v4-pro` |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | optional | Default `deepseek-v4-pro` |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | optional | Default `deepseek-v4-flash` |
| `CLAUDE_CODE_SUBAGENT_MODEL` | optional | Default `deepseek-v4-flash` |
| `CLAUDE_CODE_EFFORT_LEVEL` | optional | Default `max` |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | optional | Default `786432` |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | optional | Default `1` (also baked on the image) |

Do **not** set process-wide `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`
on the web service unless you intend every `LLM_PROVIDER=claude` call to hit
DeepSeek. Factory injects that URL into the CLI subprocess automatically.

Do **not** set `ANTHROPIC_MODEL=deepseek-v4-pro[1m]` (or the matching
`ANTHROPIC_DEFAULT_*` / `DEEPSEEK_CODE_MODEL` values) on Render. DeepSeek's
Claude Code setup page still prints the `[1m]` context-window suffix;
live Claude Code SDK rejects it as `[claude-code:unrecognized_model]` and
the Floor fail-closes as `FACTORY_CODE_CLI_MODEL_DENIED`. Use the catalog
id `deepseek-v4-pro` (https://api-docs.deepseek.com/quick_start/pricing).
DeepSeek maps `claude-opus*` → `deepseek-v4-pro`. Factory also strips a
leftover `[1m]` suffix if an old dashboard value is still set.

Leave Floor chat on OpenRouter. Do not put API keys in this repo.

## Production image install (`./Dockerfile`)

| Item | Value |
|------|--------|
| Docs | https://code.claude.com/docs/en/install |
| DeepSeek + Claude Code | https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/ |
| Installer | https://claude.ai/install.sh |
| Pin | `ARG CLAUDE_CODE_VERSION=2.1.263` |
| Install dir | `/usr/local/bin/claude` |
| Verify | `which claude` and `claude --version` inside the image (CI `docker run` as uid 10001) |
| Auto-update | `DISABLE_AUTOUPDATER=1` |

Bump the pin by changing `CLAUDE_CODE_VERSION` (and rebuild). Headless Floor
uses `claude --print "<coder_brief.md body>" --dangerously-skip-permissions`
and pipes the same brief on stdin (official `cat brief | claude -p "query"`).
A trailing `@docs/coder_brief.md` mention is **not** a prompt — Claude Code
2.1.x then exits `Input must be provided either through stdin or as a prompt
argument when using --print`. Do not use Kimi `--prompt`.

## Local / Cloud Agent

```bash
# Required for DeepSeek C-BRIEF (never commit the key)
export DEEPSEEK_API_KEY='sk-...'
export FACTORY_CODE_CLI=claude
# optional; default when DEEPSEEK_API_KEY is set
# export FACTORY_CODE_PROVIDER=deepseek
```

Install Claude Code on the host (`claude` on `PATH`) or use the production
image. `backend/.env.example` lists the same names as comments.

## Role reminder

| Actor | Job |
|-------|-----|
| **OpenRouter / architect chat** | Floor conversation + blueprint talk |
| **DeepSeek V4 Pro via Claude Code** | Factory Engineer C-BRIEF (one gated ≥2h-capable CLI session) |
| **Kimi Code** | Alternate FACTORY_CODE_CLI when `FACTORY_CODE_CLI=kimi` |
| **CerebrumDev.ai** | Governance, dual registry, certify, regenerate |
