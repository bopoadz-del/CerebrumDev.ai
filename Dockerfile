FROM python:3.11-slim-bookworm

WORKDIR /app

# Build deps for chromadb/hnswlib. Neon is Postgres 18; Debian bookworm's
# postgresql-client is 15 and pg_dump then exits 1 (version mismatch).
# Install the PGDG 18 client so nightly dumps match the server.
# Write the PGDG ASCII key and point apt signed-by at it. Slim images often
# omit the gpg binary even when gnupg is listed, which failed CI in ~20s.
# SQLAlchemy fallback in app.core.backup still covers a mismatch if apt pins drift.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ cmake git ca-certificates curl util-linux bash \
    && mkdir -p /usr/share/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/keyrings/pgdg.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.lock backend/requirements.txt backend/requirements-marker.txt ./
# Install the locked graph, not a floating requirements.txt resolve.
# requirements.txt remains the direct manifest; lock is the image contract.
# Do NOT install requirements-marker.txt in production images.
# marker-pdf is local-only; see README for details.
RUN pip install --no-cache-dir -r requirements.lock

# Official Kimi Code CLI (FACTORY_CODE_CLI=kimi). Standalone glibc binary;
# no Node.js. Pin via KIMI_CODE_VERSION. Docs + installer:
#   https://www.kimi.com/code/docs/en/kimi-code-cli/guides/getting-started
#   https://code.kimi.com/kimi-code/install.sh
#   KIMI_VERSION=… KIMI_INSTALL_DIR=/usr/local  →  /usr/local/bin/kimi
# Credentials stay out of the image: KIMI_CODE_API_KEY writes
# ~/.kimi-code/config.toml at boot (providers + default_model; see
# docs/factory/KIMI_ENV_SETUP.md). Headless Floor cannot run /login.
ARG KIMI_CODE_VERSION=0.41.0
# Official installer still owns the pin + checksum. Its _download uses bare
# curl, and code.kimi.com's HTTP/2 often RST mid-binary on GHA
# (curl 18: "HTTP/2 stream 1 was not closed cleanly" — CI run 33994965484).
# Retry + HTTP/1.1 on install.sh and on every curl the installer issues.
# Do not stub kimi; the image smoke still requires /usr/local/bin/kimi.
RUN set -eu \
    && printf '%s\n' \
         '#!/bin/sh' \
         'exec /usr/bin/curl --retry 5 --retry-all-errors --retry-delay 2 --http1.1 "$@"' \
         > /tmp/curl \
    && chmod +x /tmp/curl \
    && PATH="/tmp:${PATH}" \
       curl -fsSL https://code.kimi.com/kimi-code/install.sh \
         -o /tmp/kimi-code-install.sh \
    && PATH="/tmp:${PATH}" \
       KIMI_VERSION="${KIMI_CODE_VERSION}" \
       KIMI_INSTALL_DIR=/usr/local \
       KIMI_NO_MODIFY_PATH=1 \
       KIMI_CODE_HOME=/tmp/kimi-code-home \
       bash /tmp/kimi-code-install.sh \
    && rm -f /tmp/kimi-code-install.sh /tmp/curl \
    && rm -rf /tmp/kimi-code-home /root/.kimi-code \
    && test -x /usr/local/bin/kimi \
    && /usr/local/bin/kimi --version

# Leftover official Claude Code CLI. DeepSeek V4 Pro is NOT reached
# through this binary — Factory coding uses FACTORY_CODE_CLI=kimi +
# DEEPSEEK_API_KEY (OpenAI-compat https://api.deepseek.com). Kept in the
# image as unused leftover so existing pins do not break the build.
# Native installer, pinned. Docs:
#   https://code.claude.com/docs/en/install
#   curl -fsSL https://claude.ai/install.sh | bash -s <version>
# The launcher is a symlink into ~/.local/share/claude/versions/; copy the
# resolved binary to /usr/local/bin so appuser (HOME=/app) can exec it.
# DISABLE_AUTOUPDATER keeps the pin; do not stub claude.
ARG CLAUDE_CODE_VERSION=2.1.263
ENV DISABLE_AUTOUPDATER=1
ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
RUN set -eu \
    && printf '%s\n' \
         '#!/bin/sh' \
         'exec /usr/bin/curl --retry 5 --retry-all-errors --retry-delay 2 --http1.1 "$@"' \
         > /tmp/curl \
    && chmod +x /tmp/curl \
    && PATH="/tmp:${PATH}" \
       curl -fsSL https://claude.ai/install.sh \
         -o /tmp/claude-code-install.sh \
    && HOME=/tmp/claude-code-home \
       PATH="/tmp:${PATH}" \
       bash /tmp/claude-code-install.sh "${CLAUDE_CODE_VERSION}" \
    && src="$(readlink -f /tmp/claude-code-home/.local/bin/claude 2>/dev/null || true)" \
    && if [ -z "${src}" ] || [ ! -f "${src}" ]; then \
         src="$(find /tmp/claude-code-home -type f -name claude | head -n 1)"; \
       fi \
    && test -n "${src}" && test -f "${src}" \
    && install -m 0755 "${src}" /usr/local/bin/claude \
    && rm -f /tmp/claude-code-install.sh /tmp/curl \
    && rm -rf /tmp/claude-code-home /root/.claude /root/.local/share/claude \
    && test -x /usr/local/bin/claude \
    && /usr/local/bin/claude --version

COPY backend/app /app/app
# Golden product blueprints (Steward + examples) — required by ProductArchitect in prod
COPY blueprints /app/blueprints
# Factory store pin. factory_repo_root() is /app in this image, so
# default_lock_path() is /app/blocks.lock.json. S07 wrote the lock at the
# repo root but the image never copied it; CLONER then treated every
# store-sourced block as unlocked (live Steward Continue sess_5782f226
# died at database with the lock-hash that was already committed).
COPY blocks.lock.json /app/blocks.lock.json
# Alembic migration system for the accounts DB (runs at boot; see backend/alembic/)
COPY backend/alembic.ini /app/alembic.ini
COPY backend/alembic /app/alembic

# Operational entry points: the backup CLI (manual snapshot/restore/drill; the
# nightly backup itself runs IN-PROCESS in the web service, not as a cron —
# Render cron jobs cannot mount the disk) and the Postgres cutover script.
# Without this COPY those tools fail at import.
COPY backend/scripts /app/scripts

# S0 preflight fingerprints repo-relative paths from factory_repo_root()
# (/app in this image): backend/app/factory/**, cerebrum_product_kernel, and
# .github/workflows/ci.yml. COPY backend/app → /app/app drops the backend/
# prefix those paths use, and never shipped ci.yml. Plant both so
# factory_source_missing cannot fire on Approve & build.
RUN mkdir -p /app/backend && ln -s /app/app /app/backend/app
COPY .github/workflows/ci.yml /app/.github/workflows/ci.yml

# FACTORY_CODE_CLI=kimi → /usr/local/bin/kimi (DeepSeek V4 Pro when
# DEEPSEEK_API_KEY is set; Moonshot when FACTORY_CODE_PROVIDER=kimi).
# Leftover /usr/local/bin/claude is unused for Factory DeepSeek coding.
# A keyed Floor still fail-closes FACTORY_CODE_CLI_UNAVAILABLE if the
# selected executable is missing. DEEPSEEK_API_KEY at boot writes
# [providers.deepseek] into ~/.kimi-code/config.toml. KIMI_CODE_API_KEY
# writes [providers.kimi]. Do not set process-wide ANTHROPIC_* — Floor
# chat stays on OpenRouter. See docs/factory/DEEPSEEK_ENV_SETUP.md.
ENV PORT=8000
# libpq defaults sslcert to $HOME/.postgresql/postgresql.crt. python:slim
# leaves HOME=/root. After the entrypoint drops to uid 10001 that path is
# EACCES and Alembic/Neon boot dies — Neon only needs sslmode=require.
# HOME=/app is owned by appuser; do not set PGSSLCERT.
ENV HOME=/app
# Non-root runtime. The entrypoint chowns $STORAGE_PATH when started as root
# (Render disk mounts are often root-owned) then drops to uid 10001.
# The HTTP contract is unchanged: bind 0.0.0.0:$PORT after migrations.
RUN useradd --system --uid 10001 --home-dir /app --no-create-home appuser \
    && mkdir -p /app/storage \
    && chown -R appuser:appuser /app
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 755 /app/docker-entrypoint.sh
ENTRYPOINT ["/app/docker-entrypoint.sh"]
# Apply accounts-DB migrations before serving (no-op when already at head).
# A failed migration must stop the boot. The previous form swallowed the
# failure with `|| echo`, which produced a RUNNING service on the wrong schema:
# /health still answered 200, Render never restarted it, no alert fired, and
# users got 500s from any path touching a missing column. Crashing is louder
# and safer -- Render surfaces a failed deploy and keeps the previous version
# serving.
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
