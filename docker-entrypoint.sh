#!/bin/sh
# Production image entrypoint: ensure the mounted disk is writable, then drop
# to the non-root appuser. Staying root for the whole process is the old
# contract; this keeps boot working when Render mounts /app/storage as root.
set -e
STORAGE="${STORAGE_PATH:-/app/storage}"
mkdir -p "$STORAGE" "$STORAGE/factory_outputs"

# Factory workspaces + build ledgers must survive deploys. The Render disk is
# only at $STORAGE_PATH; /app itself is ephemeral. Point the legacy absolute
# path /app/factory_outputs at the disk so (a) factory_outputs_root() via
# STORAGE_PATH and (b) session-baked /app/factory_outputs/... paths both land
# on cerebrumdev-storage. Without this, HANDOFF_TO_N3 ledgers vanish on every
# release and Continue re-enters WRITER thrash.
_FO_LINK=/app/factory_outputs
_FO_REAL="$STORAGE/factory_outputs"
if [ -L "$_FO_LINK" ]; then
  :
elif [ ! -e "$_FO_LINK" ]; then
  ln -s "$_FO_REAL" "$_FO_LINK"
elif [ -d "$_FO_LINK" ]; then
  # Ephemeral real dir (image layer or pre-fix boot): merge onto disk, replace.
  find "$_FO_LINK" -mindepth 1 -maxdepth 1 -exec mv -t "$_FO_REAL/" {} + 2>/dev/null || true
  rmdir "$_FO_LINK" 2>/dev/null || rm -rf "$_FO_LINK"
  ln -s "$_FO_REAL" "$_FO_LINK"
fi

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
  # Symlink itself must stay readable by appuser (parent /app is already owned).
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=appuser --regid=appuser --init-groups -- "$@"
  fi
  exec su -s /bin/sh appuser -c 'export HOME=/app; exec "$@"' -- "$@"
fi
exec "$@"
