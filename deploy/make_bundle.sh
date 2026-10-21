#!/usr/bin/env bash
# Build a self-contained deployment bundle of the published corpus.
# Runs on the development machine; reads Qdrant on 127.0.0.1:6333.
# Output: dist/deploy-<corpus>/ with images, Qdrant snapshot, data and code.
# Never packs .env (secrets are created on the server).
set -euo pipefail
cd "$(dirname "$0")/.."

QDRANT=${QDRANT:-http://127.0.0.1:6333}
APP_IMAGE=${APP_IMAGE:-env-reg-rag-app:9097}
CORPUS=$(python -c "import json;print(json.load(open('data/registry/corpus-current.json',encoding='utf-8'))['corpus_version'])")
COLLECTION=$(python -c "import json;print(json.load(open('data/registry/corpus-current.json',encoding='utf-8'))['collection_name'])")
OUT="dist/deploy-$CORPUS"
mkdir -p "$OUT"

echo "[bundle] snapshot $COLLECTION"
SNAP=$(curl -sf -X POST "$QDRANT/collections/$COLLECTION/snapshots?wait=true" \
  | python -c "import json,sys;print(json.load(sys.stdin)['result']['name'])")
curl -sf -o "$OUT/collection.snapshot" "$QDRANT/collections/$COLLECTION/snapshots/$SNAP"
curl -sf -X DELETE "$QDRANT/collections/$COLLECTION/snapshots/$SNAP" >/dev/null
echo "$COLLECTION" > "$OUT/collection.name"

echo "[bundle] images"
docker save "$APP_IMAGE" qdrant/qdrant:v1.19.1 | gzip -1 > "$OUT/images.tar.gz"
echo "$APP_IMAGE" > "$OUT/app.image"

echo "[bundle] code (git HEAD) and runtime data"
git archive --format=tar.gz -o "$OUT/code.tar.gz" HEAD
tar -czf "$OUT/data.tar.gz" data/registry data/raw

cp deploy/server_install.sh "$OUT/"
( cd "$OUT" && sha256sum *.tar.gz collection.snapshot > SHA256SUMS )
du -sh "$OUT"/*
echo "[bundle] done: $OUT"
