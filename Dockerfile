FROM python:3.11-slim

WORKDIR /app

# Build deps for chromadb/hnswlib. Neon is Postgres 18; Debian bookworm's
# postgresql-client is 15/16 and pg_dump then exits 1 (version mismatch).
# Install the PGDG 18 client so nightly dumps match the server. SQLAlchemy
# fallback in app.core.backup still covers a mismatch if apt pins drift.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ cmake git ca-certificates curl gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-marker.txt ./
# Do NOT install requirements-marker.txt in production images.
# marker-pdf is local-only; see README for details.
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app /app/app
# Golden product blueprints (Steward + examples) — required by ProductArchitect in prod
COPY blueprints /app/blueprints
# Alembic migration system for the accounts DB (runs at boot; see backend/alembic/)
COPY backend/alembic.ini /app/alembic.ini
COPY backend/alembic /app/alembic

# Operational entry points: the backup CLI (manual snapshot/restore/drill; the
# nightly backup itself runs IN-PROCESS in the web service, not as a cron —
# Render cron jobs cannot mount the disk) and the Postgres cutover script.
# Without this COPY those tools fail at import.
COPY backend/scripts /app/scripts

ENV PORT=8000
# Apply accounts-DB migrations before serving (no-op when already at head).
# A failed migration must stop the boot. The previous form swallowed the
# failure with `|| echo`, which produced a RUNNING service on the wrong schema:
# /health still answered 200, Render never restarted it, no alert fired, and
# users got 500s from any path touching a missing column. Crashing is louder
# and safer -- Render surfaces a failed deploy and keeps the previous version
# serving.
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
