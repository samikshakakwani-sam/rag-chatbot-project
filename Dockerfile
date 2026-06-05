# ── FundFacts AI — production image ─────────────────────────────────────────
# Self-contained: bakes the BGE-small model + the ChromaDB vector store into the
# image so the container starts fast and works offline (except the Groq API).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence_transformers \
    LLM_PROVIDER=groq \
    PORT=7860

WORKDIR /app

# Python dependencies — full stack (torch via sentence-transformers; CPU wheels).
COPY requirements-docker.txt .
RUN pip install -r requirements-docker.txt

# Application code + the minimal data the runtime needs (chunks.json only;
# raw HTML and the prior chroma store are excluded via .dockerignore).
COPY backend/ backend/
COPY ingestion/ ingestion/
COPY frontend/ frontend/
COPY data/chunks.json data/chunks.json

# Pre-cache the embedding model and build the persistent vector store at build
# time → no cold-start model download, no first-request latency spike.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')" \
 && python -m ingestion.embed

EXPOSE 7860

# $PORT is provided by most PaaS (Render, etc.); defaults to 7860 (HF Spaces).
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
