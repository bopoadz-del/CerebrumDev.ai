#!/usr/bin/env bash
# Configure Kimi Code CLI credentials only (NOT the in-app chat LLM).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${1:-}"
if [[ -z "$KEY" ]]; then
  echo "Usage: $0 'sk-your-kimi-code-key'" >&2
  exit 1
fi
ENV_FILE="$ROOT/backend/.env"
touch "$ENV_FILE"
grep -vE '^(KIMI_CODE_|KIMI_API_KEY=|CEREBRUM_LLM_|LLM_PROVIDER=kimi)' "$ENV_FILE" > "${ENV_FILE}.tmp" || true
mv "${ENV_FILE}.tmp" "$ENV_FILE"
cat >> "$ENV_FILE" <<EOF
# Kimi Code CLI (Factory Engineer / Store Manager) — not LLM_PROVIDER
KIMI_CODE_API_KEY=${KEY}
KIMI_CODE_BASE_URL=https://api.moonshot.ai/v1
EOF
echo "Wrote KIMI_CODE_* to backend/.env (gitignored). LLM_PROVIDER unchanged."
