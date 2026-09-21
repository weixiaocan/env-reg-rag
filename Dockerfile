FROM python:3.11-slim

ARG BGE_MODEL_REVISION=7999e1d3359715c523056ef9478215996d62a620

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.14.0 \
    && python -m pip install -r requirements.txt

# Pin the embedding artifact in the image so runtime readiness never depends on
# Hugging Face availability or an unversioned model update.
RUN python -c "from transformers import AutoModel, AutoTokenizer; model='BAAI/bge-small-zh-v1.5'; revision='${BGE_MODEL_REVISION}'; AutoTokenizer.from_pretrained(model, revision=revision); AutoModel.from_pretrained(model, revision=revision)"

COPY src ./src
COPY data/registry ./data/registry
COPY data/canonical ./data/canonical

RUN mkdir -p /app/data/observability \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data/observability

ENV TRANSFORMERS_OFFLINE=1

USER appuser

# No V2 retrieval HTTP service is served from this image yet. The image is kept
# as a base with the bge embedding artifact pinned and the V2 src/ + canonical
# corpus available, so a future V2 retrieval service can build on it. Run V2
# ingestion via: python scripts/ingest_corpus_v2.py
CMD ["python", "-c", "print('env-reg-rag base image; no V2 retrieval service yet')"]
