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

ENV PORT=8000
# Apply accounts-DB migrations before serving (no-op when already at head);
# a migration failure never blocks boot — the app logs and continues.
CMD ["sh", "-c", "python -m alembic upgrade head || echo 'alembic upgrade skipped (see logs)'; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
