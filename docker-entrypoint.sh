#!/bin/sh
# Production image entrypoint: ensure the mounted disk is writable, then drop
# to the non-root appuser. Staying root for the whole process is the old
# contract; this keeps boot working when Render mounts /app/storage as root.
set -e
STORAGE="${STORAGE_PATH:-/app/storage}"
mkdir -p "$STORAGE"
if [ "$(id -u)" = "0" ]; then
  chown -R appuser:appuser "$STORAGE" 2>/dev/null || true
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=appuser --regid=appuser --init-groups -- "$@"
  fi
  exec su -s /bin/sh appuser -c 'exec "$@"' -- "$@"
fi
exec "$@"
