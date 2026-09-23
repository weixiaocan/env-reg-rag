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

# Runtime corpus / PDF / formula artifacts are bind-mounted by compose.
RUN mkdir -p \
        /app/data/registry \
        /app/data/canonical \
        /app/data/raw \
        /app/data/model_runtime \
        /app/data/observability \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data

ENV TRANSFORMERS_OFFLINE=1 \
    QDRANT_URL=http://qdrant:6333 \
    QDRANT_COLLECTION=corpus_v2

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.server.main:app", "--host", "0.0.0.0", "--port", "8000"]