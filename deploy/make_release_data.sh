#!/usr/bin/env bash
# Build the data assets attached to a GitHub Release, so a self-hoster can run
# the published corpus without re-running OCR and ingestion.
# Runs on the development machine; reads Qdrant on 127.0.0.1:6333.
# Output: dist/release-<corpus>/ with the Qdrant snapshot, data archive and checksums.
# Code is not packed (it comes from git) and neither is .env.
set -euo pipefail
cd "$(dirname "$0")/.."

QDRANT=${QDRANT:-http://127.0.0.1:6333}
CORPUS=$(python -c "import json;print(json.load(open('data/registry/corpus-current.json',encoding='utf-8'))['corpus_version'])")
COLLECTION=$(python -c "import json;print(json.load(open('data/registry/corpus-current.json',encoding='utf-8'))['collection_name'])")
OUT="dist/release-$CORPUS"
mkdir -p "$OUT"

echo "[release] snapshot $COLLECTION"
SNAP=$(curl -sf -X POST "$QDRANT/collections/$COLLECTION/snapshots?wait=true" \
  | python -c "import json,sys;print(json.load(sys.stdin)['result']['name'])")
curl -sf -o "$OUT/qdrant-$CORPUS.snapshot" "$QDRANT/collections/$COLLECTION/snapshots/$SNAP"
curl -sf -X DELETE "$QDRANT/collections/$COLLECTION/snapshots/$SNAP" >/dev/null

echo "[release] registry and original PDFs"
# The registry pins which corpus is live and where each PDF is; data/raw holds
# the PDFs the reader renders page images from.
tar -czf "$OUT/data-$CORPUS.tar.gz" data/registry data/raw

( cd "$OUT" && sha256sum "data-$CORPUS.tar.gz" "qdrant-$CORPUS.snapshot" > SHA256SUMS )
du -sh "$OUT"/*
echo "[release] done: $OUT  (collection $COLLECTION)"
