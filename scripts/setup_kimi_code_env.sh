#!/usr/bin/env bash
# Configure Kimi Code CLI credentials (writes config.toml — NOT in-app chat LLM).
#
# Kimi Code CLI reads provider keys only from config.toml
# (see https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files).
# Shell exports / backend/.env KIMI_* are NOT used by the CLI for auth.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${1:-}"
BASE_URL="${KIMI_CODE_BASE_URL:-https://api.moonshot.ai/v1}"
# Kimi Code CLI 0.41 config-files complete example. Override for other catalogs.
MODEL="${KIMI_CODE_MODEL:-kimi-code/k3}"

if [[ -z "$KEY" ]]; then
  echo "Usage: $0 'sk-your-kimi-code-key'" >&2
  exit 1
fi

# Optional project-local note (does NOT authenticate the CLI; docs only)
ENV_FILE="$ROOT/backend/.env"
touch "$ENV_FILE"
# Do NOT strip KIMI_API_KEY / CEREBRUM_LLM_* / LLM_PROVIDER — those are in-app LLM settings.
if grep -q '^KIMI_CODE_API_KEY=' "$ENV_FILE" 2>/dev/null; then
  grep -vE '^KIMI_CODE_(API_KEY|BASE_URL|MODEL)=' "$ENV_FILE" > "${ENV_FILE}.tmp" || true
  mv "${ENV_FILE}.tmp" "$ENV_FILE"
fi
cat >> "$ENV_FILE" <<EOF
# Reference only — Kimi Code CLI auth is in config.toml (below), not this file.
KIMI_CODE_API_KEY=${KEY}
KIMI_CODE_BASE_URL=${BASE_URL}
KIMI_CODE_MODEL=${MODEL}
EOF

# CLI credentials: ~/.kimi-code/config.toml (or $KIMI_CODE_HOME/config.toml)
CODE_HOME="${KIMI_CODE_HOME:-${HOME}/.kimi-code}"
mkdir -p "$CODE_HOME"
CONFIG_TOML="${CODE_HOME}/config.toml"

python3 - "$CONFIG_TOML" "$KEY" "$BASE_URL" "$MODEL" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
base_url = sys.argv[3]
alias = sys.argv[4] or "kimi-code/k3"
model_id = alias.rsplit("/", 1)[-1]
ctx = 1048576 if model_id == "k3" else 262144
text = path.read_text(encoding="utf-8") if path.exists() else ""

block = f'''[providers.kimi]
type = "kimi"
api_key = "{key}"
base_url = "{base_url}"
'''

# Replace existing [providers.kimi] section if present; else append.
pattern = re.compile(
    r"(?ms)^\[providers\.kimi\][^\[]*(?=^\[|\Z)",
)
if pattern.search(text):
    text = pattern.sub(block.rstrip() + "\n\n", text)
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text = text + ("\n" if text else "") + block

if not re.search(r'(?m)^\s*default_model\s*=\s*"[^"]+"', text):
    text = re.sub(r"(?m)^\s*default_model\s*=.*\n?", "", text)
    body = text.lstrip("\n")
    text = f'default_model = "{alias}"\n' + ("\n" + body if body else "")

quoted = f'[models."{alias}"]'
if quoted not in text:
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    text += (
        f'[models."{alias}"]\n'
        'provider = "kimi"\n'
        f'model = "{model_id}"\n'
        f"max_context_size = {ctx}\n"
    )

path.write_text(text, encoding="utf-8")
print(f"Wrote Kimi Code provider credentials + default_model={alias} to {path}")
print("In-app LLM env (LLM_PROVIDER / CEREBRUM_LLM_* / KIMI_API_KEY) left unchanged.")
PY
