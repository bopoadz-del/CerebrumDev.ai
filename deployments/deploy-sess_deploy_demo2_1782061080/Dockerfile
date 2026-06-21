# Deployed Cerebrum-Blocks instance with injected domain container and chain.
FROM python:3.11-slim
WORKDIR /app

# System deps for Cerebrum-Blocks (OCR, PDF, build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential gcc g++ gfortran pkg-config \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    poppler-utils tesseract-ocr libtesseract-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Clone Cerebrum-Blocks runtime
RUN git clone --depth 1 https://github.com/bopoadz-del/Cerebrum-Blocks.git /app

# Install Python deps
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Inject session-specific files and patch the runtime
COPY . /app
RUN python patch_blocks.py

# Restore vector index
RUN python bootstrap.py

ENV PORT=8000
EXPOSE 8000
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
