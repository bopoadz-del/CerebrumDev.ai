# cerebrum-cli

Thin terminal client for CerebrumDev.ai instances. Only runtime dependency: `httpx`.

## Install

```bash
cd cli
pip install -e .
```

## Auth

Resolution order: CLI flag > environment variable > `~/.cerebrum/config.toml`.

| Mode | Flag | Env |
|------|------|-----|
| JWT token | `--token` | `CEREBRUM_TOKEN` |
| API key | `--api-key` | `CEREBRUM_API_KEY` |
| Email/password | `--email` / `--password` | `CEREBRUM_EMAIL` / `CEREBRUM_PASSWORD` |

Email/password mode POSTs to `/v1/users/login` and caches the returned token in-process.

## Config

```bash
cerebrum init              # interactive config writer
cerebrum config            # show resolved config (api_key masked)
```

`~/.cerebrum/config.toml`:

```toml
base_url = "https://cerebrumdev-backend.onrender.com"
api_key = "cb_prod_..."
domain = "construction"
instance_name = "prod"
session_id = "sess_..."
```

## Usage

```bash
cerebrum health
cerebrum chat "list blocks" --session sess_abc123
cerebrum chat --repl --session sess_abc123
cerebrum chat "list blocks" --events   # show heartbeats/first-token marks, suppress tokens
cerebrum chat "list blocks" --raw      # dump raw SSE data lines
cerebrum upload file1.pdf file2.txt --session sess_abc123
cerebrum chain show --session sess_abc123
cerebrum deploy status --session sess_abc123
```

## Notes

- The CLI targets the canonical SSE envelope (`data:` lines with JSON `type`).
- CerebrumDev's configurator router currently uses a different named-event format; a future Spec 2 PR standardizes the servers.
- `reindex` and `rules list` are reserved for deployed-instance endpoints and print a clear "not available" message until those endpoints exist.
