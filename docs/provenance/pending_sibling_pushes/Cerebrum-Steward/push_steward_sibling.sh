#!/usr/bin/env bash
# Run AFTER creating private repo bopoadz-del/Cerebrum-Steward with a token that can push.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
SRC="$ROOT/factory_outputs/Cerebrum-Steward"
test -d "$SRC" || { echo "Generate first: factory cli generate steward"; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cp -a "$SRC/." "$TMP/"
cd "$TMP"
git init
git checkout -b main
git add -A
git -c user.email=factory@cerebrumdev.ai -c user.name='CerebrumDev Factory' commit -m "chore: initial Factory-generated Cerebrum Steward"
git remote add origin "https://github.com/bopoadz-del/Cerebrum-Steward.git"
git push -u origin main
echo "Pushed. Connect Render using render.yaml in the repo."
