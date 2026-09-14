FROM python:3.11-slim

ARG BGE_MODEL_REVISION=7999e1d3359715c523056ef9478215996d62a620

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

COPY requirements-m3.txt requirements-m4.txt requirements-m5.txt requirements-m6.txt requirements-runtime.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.14.0 \
    && python -m pip install -r requirements-runtime.txt

# Pin the embedding artifact in the image so runtime readiness never depends on
# Hugging Face availability or an unversioned model update.
RUN python -c "from transformers import AutoModel, AutoTokenizer; model='BAAI/bge-small-zh-v1.5'; revision='${BGE_MODEL_REVISION}'; AutoTokenizer.from_pretrained(model, revision=revision); AutoModel.from_pretrained(model, revision=revision)"

COPY src ./src
COPY scripts/ensure_qdrant_indexes.py ./scripts/ensure_qdrant_indexes.py
COPY data/evidence ./data/evidence
COPY data/registry ./data/registry
COPY data/retrieval ./data/retrieval

RUN mkdir -p /app/data/observability \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data/observability

ENV TRANSFORMERS_OFFLINE=1

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
