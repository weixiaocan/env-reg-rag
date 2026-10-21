#!/usr/bin/env bash
# Restore the published corpus from GitHub Release assets into a fresh clone.
# Usage: deploy/restore_release_data.sh <directory holding the downloaded assets>
# Needs Docker; starts Qdrant from compose.yaml, loads the snapshot and points
# the live alias at it. Start the app afterwards with `docker compose up -d --build`.
set -euo pipefail
ASSETS=$(cd "${1:?usage: $0 <assets-dir>}" && pwd)
cd "$(dirname "$0")/.."
QDRANT=http://127.0.0.1:${QDRANT_HTTP_HOST_PORT:-6333}

SNAPSHOT=$(ls "$ASSETS"/qdrant-corpus-*.snapshot | head -1)
DATA=$(ls "$ASSETS"/data-corpus-*.tar.gz | head -1)
(cd "$ASSETS" && sha256sum -c SHA256SUMS)

echo "[restore] unpack registry and PDFs"
tar -xzf "$DATA"
mkdir -p data/observability data/canonical data/model_runtime
CORPUS=$(python -c "import json;print(json.load(open('data/registry/corpus-current.json',encoding='utf-8'))['corpus_version'])")
COLLECTION=$(python -c "import json;print(json.load(open('data/registry/corpus-current.json',encoding='utf-8'))['collection_name'])")
case "$SNAPSHOT" in *"$CORPUS"*) ;; *) echo "snapshot $SNAPSHOT does not match registry corpus $CORPUS"; exit 1;; esac

docker compose up -d qdrant
until curl -sf "$QDRANT/healthz" >/dev/null; do sleep 2; done

if curl -sf "$QDRANT/collections/$COLLECTION" >/dev/null; then
  echo "[restore] collection $COLLECTION already present"
else
  echo "[restore] load $COLLECTION"
  # The snapshot names its own collection; upload restores it under that name.
  curl -sf -X POST "$QDRANT/collections/$COLLECTION/snapshots/upload?priority=snapshot&wait=true" \
    -F "snapshot=@$SNAPSHOT" >/dev/null
fi
curl -sf -X POST "$QDRANT/collections/aliases" -H 'Content-Type: application/json' \
  -d '{"actions":[{"delete_alias":{"alias_name":"corpus_v2_live"}}]}' >/dev/null || true
curl -sf -X POST "$QDRANT/collections/aliases" -H 'Content-Type: application/json' \
  -d "{\"actions\":[{\"create_alias\":{\"collection_name\":\"$COLLECTION\",\"alias_name\":\"corpus_v2_live\"}}]}" >/dev/null
echo "[restore] done: $CORPUS -> corpus_v2_live. Next: docker compose up -d --build"
