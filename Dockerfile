FROM python:3.11-slim

WORKDIR /app

# Install build dependencies for chromadb/hnswlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ cmake git \
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

# Operational entry points invoked by scheduled jobs (see render.yaml):
# the nightly backup and the Postgres cutover. Without this the backup cron
# fails at import -- and since notifyOnFail only covers deploy failures, it
# would fail silently every night while appearing to be configured.
COPY backend/scripts /app/scripts

ENV PORT=8000
# Apply accounts-DB migrations before serving (no-op when already at head);
# a migration failure never blocks boot — the app logs and continues.
# A failed migration must stop the boot. The previous form swallowed the
# failure with `|| echo`, which produced a RUNNING service on the wrong schema:
# /health still answered 200, Render never restarted it, no alert fired, and
# users got 500s from any path touching a missing column. Crashing is louder
# and safer -- Render surfaces a failed deploy and keeps the previous version
# serving.
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
