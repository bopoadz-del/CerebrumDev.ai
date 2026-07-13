# Generated RetailOps platform image.
# Build from the repository root:
#   docker build -f backend/app/retailops/deploy/Dockerfile -t retailops .
#
# Single hardened package: generated runtime routes, business frontend,
# migrations, PostgreSQL/pgvector. Runs as a non-root user, exposes /health,
# and applies mandatory migrations on startup (startup fails if they fail).

# --- Stage 1: build the business frontend ---
FROM node:20-slim AS frontend
WORKDIR /fe
COPY backend/app/retailops/frontend/package*.json ./
RUN npm ci
COPY backend/app/retailops/frontend/ ./
RUN npm run build

# --- Stage 2: python runtime ---
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    RETAILOPS_EMBED_BACKEND=hash

WORKDIR /app

COPY backend/app/retailops/deploy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code (the FastAPI package root is /app/app).
COPY backend/app /app/app
COPY backend/app/retailops/deploy/build_metadata.json /app/build_metadata.json

# Built frontend served by the runtime.
COPY --from=frontend /fe/dist /app/app/retailops/frontend/dist

# Non-root user.
RUN useradd --create-home --uid 10001 retailops && chown -R retailops:retailops /app
USER retailops

EXPOSE 8000
# Bind to 0.0.0.0:$PORT (Render provides $PORT).
CMD ["sh", "-c", "uvicorn app.retailops.app_factory:app --host 0.0.0.0 --port ${PORT:-8000}"]
