#!/usr/bin/env bash
# Install or upgrade the RAG service on a Linux server with Docker + compose v2.
# Usage (inside the uploaded bundle dir): bash server_install.sh /opt/drainage-rag
# The target dir must already contain .env (see deploy/README.md).
set -euo pipefail
BUNDLE=$(cd "$(dirname "$0")" && pwd)
TARGET=${1:-/opt/drainage-rag}
QDRANT=http://127.0.0.1:6333

(cd "$BUNDLE" && sha256sum -c SHA256SUMS)
[ -f "$TARGET/.env" ] || { echo "missing $TARGET/.env -- create it first"; exit 1; }

echo "[install] load images"
gunzip -c "$BUNDLE/images.tar.gz" | docker load
echo "[install] unpack code and data into $TARGET"
tar -xzf "$BUNDLE/code.tar.gz" -C "$TARGET"
tar -xzf "$BUNDLE/data.tar.gz" -C "$TARGET"
mkdir -p "$TARGET/data/observability" "$TARGET/data/canonical" "$TARGET/data/model_runtime"
chown -R 10001:10001 "$TARGET/data/observability"

cd "$TARGET"
export APP_IMAGE_NAME=$(cat "$BUNDLE/app.image")
docker compose up -d qdrant
until curl -sf "$QDRANT/healthz" >/dev/null; do sleep 2; done

COLLECTION=$(cat "$BUNDLE/collection.name")
if curl -sf "$QDRANT/collections/$COLLECTION" >/dev/null; then
  echo "[install] collection $COLLECTION already present"
else
  echo "[install] restore $COLLECTION"
  curl -sf -X POST "$QDRANT/collections/$COLLECTION/snapshots/upload?priority=snapshot&wait=true" \
    -F "snapshot=@$BUNDLE/collection.snapshot" >/dev/null
fi

# Do not expose an incomplete collection. Switch the alias in one Qdrant action batch.
EXPECTED_POINTS=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["chunk_count"])' "$TARGET/data/registry/corpus-current.json")
ACTUAL_POINTS=$(curl -sf "$QDRANT/collections/$COLLECTION" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["points_count"])')
[ "$ACTUAL_POINTS" = "$EXPECTED_POINTS" ] || {
  echo "[install] point mismatch: expected $EXPECTED_POINTS, actual $ACTUAL_POINTS" >&2
  exit 1
}
CURRENT_ALIAS_COLLECTION=$(curl -sf "$QDRANT/aliases" | python3 -c 'import json,sys; print(next((a["collection_name"] for a in json.load(sys.stdin)["result"]["aliases"] if a["alias_name"] == "corpus_v2_live"), ""))')
if [ "$CURRENT_ALIAS_COLLECTION" != "$COLLECTION" ]; then
  ALIAS_ACTIONS=$(python3 -c 'import json,sys; old,new=sys.argv[1:]; actions=([{"delete_alias":{"alias_name":"corpus_v2_live"}}] if old else []); actions.append({"create_alias":{"collection_name":new,"alias_name":"corpus_v2_live"}}); print(json.dumps({"actions":actions}))' "$CURRENT_ALIAS_COLLECTION" "$COLLECTION")
  curl -sf -X POST "$QDRANT/collections/aliases" -H 'Content-Type: application/json' \
    -d "$ALIAS_ACTIONS" >/dev/null
fi

docker compose up -d --no-build app
for i in $(seq 1 60); do
  READY=$(curl -s http://127.0.0.1:${APP_HTTP_HOST_PORT:-8000}/api/v1/ready || true)
  case "$READY" in *'"status":"ready"'*) echo "[install] $READY"; exit 0;; esac
  sleep 3
done
echo "[install] app not ready: $READY"; docker compose logs --tail 50 app; exit 1
