#!/bin/sh
# Production image entrypoint: ensure the mounted disk is writable, then drop
# to the non-root appuser. Staying root for the whole process is the old
# contract; this keeps boot working when Render mounts /app/storage as root.
set -e
STORAGE="${STORAGE_PATH:-/app/storage}"
mkdir -p "$STORAGE"

# libpq defaults sslcert to $HOME/.postgresql/postgresql.crt. python:slim
# leaves HOME=/root. After we drop to uid 10001, /root is mode 0700 and
# opening that default path is EACCES — which libpq treats as a hard SSL
# failure even when Neon only needs sslmode=require (no client cert).
# Point HOME at the app dir (owned by appuser) and drop client-cert env
# that would still target /root after the uid change.
export HOME=/app
case "${PGSSLCERT:-}" in
  /root/*) unset PGSSLCERT ;;
esac
case "${PGSSLKEY:-}" in
  /root/*) unset PGSSLKEY ;;
esac
if [ -n "${PGSSLCERT:-}" ] && [ ! -r "$PGSSLCERT" ]; then
  unset PGSSLCERT
fi
if [ -n "${PGSSLKEY:-}" ] && [ ! -r "$PGSSLKEY" ]; then
  unset PGSSLKEY
fi

if [ "$(id -u)" = "0" ]; then
  chown -R appuser:appuser "$STORAGE" 2>/dev/null || true
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=appuser --regid=appuser --init-groups -- "$@"
  fi
  exec su -s /bin/sh appuser -c 'export HOME=/app; exec "$@"' -- "$@"
fi
exec "$@"
