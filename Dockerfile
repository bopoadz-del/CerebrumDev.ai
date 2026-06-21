FROM python:3.11-slim

WORKDIR /app

# Install build dependencies for chromadb/hnswlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ cmake \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app /app/app

ENV PORT=8000
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
