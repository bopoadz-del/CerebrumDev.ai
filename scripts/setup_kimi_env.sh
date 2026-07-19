#!/usr/bin/env bash
# Configure Kimi / Moonshot for Factory Product Architect (local .env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${1:-}"
if [[ -z "$KEY" ]]; then
  echo "Usage: $0 'sk-your-kimi-or-moonshot-key'" >&2
  exit 1
fi
ENV_FILE="$ROOT/backend/.env"
touch "$ENV_FILE"
# Strip prior kimi/moonshot lines then append
grep -vE '^(KIMI_|CEREBRUM_LLM_|LLM_PROVIDER=)' "$ENV_FILE" > "${ENV_FILE}.tmp" || true
mv "${ENV_FILE}.tmp" "$ENV_FILE"
cat >> "$ENV_FILE" <<EOF
LLM_PROVIDER=kimi
KIMI_API_KEY=${KEY}
KIMI_BASE_URL=https://api.moonshot.cn/v1
KIMI_MODEL=moonshot-v1-8k
CEREBRUM_LLM_API_KEY=${KEY}
CEREBRUM_LLM_BASE_URL=https://api.moonshot.cn/v1
CEREBRUM_LLM_MODEL=moonshot-v1-8k
EOF
echo "Wrote Kimi config to backend/.env (gitignored)."
