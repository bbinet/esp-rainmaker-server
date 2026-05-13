#!/usr/bin/env bash
# Initialise a single-node Garage instance running under docker compose.
#
# Garage doesn't auto-init: out of the box the layout is empty and there
# are no keys or buckets. This script:
#   1. Reads the node ID
#   2. Assigns a single-zone layout
#   3. Creates an access key
#   4. Creates the OTA bucket and grants the key full access
#
# Writes the resulting credentials to var/garage-credentials.env so
# docker compose can pick them up on the next `up`.

set -euo pipefail

CONTAINER="${1:-rainmaker-garage-1}"
BUCKET="${RM_MINIO_BUCKET_OTA:-rainmaker-ota}"
CREDS_FILE="${2:-var/garage-credentials.env}"

run() { docker exec "$CONTAINER" /garage "$@"; }

echo "Waiting for Garage to be ready..."
for _ in {1..30}; do
  if run status >/dev/null 2>&1; then break; fi
  sleep 1
done

NODE_ID=$(run status 2>/dev/null | awk '/^[a-f0-9]+ /{print $1; exit}')
if [[ -z "$NODE_ID" ]]; then
  echo "Could not read Garage node id"
  run status || true
  exit 1
fi
echo "Garage node: $NODE_ID"

# Assign layout (idempotent: skip if already assigned).
if ! run layout show 2>/dev/null | grep -q "$NODE_ID"; then
  run layout assign -z dc1 -c 1G "$NODE_ID"
  run layout apply --version 1
  echo "Layout applied"
else
  echo "Layout already assigned"
fi

# Reuse existing key if present.
KEY_NAME="rainmaker-key"
EXISTING=$(run key list 2>/dev/null | awk -v n="$KEY_NAME" '$2==n{print $1}' | head -1)
if [[ -n "$EXISTING" ]]; then
  echo "Reusing key id $EXISTING"
  KEY_ID="$EXISTING"
  KEY_INFO=$(run key info "$KEY_ID" --show-secret 2>/dev/null)
else
  KEY_INFO=$(run key create "$KEY_NAME" 2>/dev/null)
  KEY_ID=$(echo "$KEY_INFO" | awk -F': *' '/^Key ID:/{print $2; exit}')
fi
SECRET=$(echo "$KEY_INFO" | awk -F': *' '/^Secret key:/{print $2; exit}')

# Bucket.
if ! run bucket list 2>/dev/null | grep -q "$BUCKET"; then
  run bucket create "$BUCKET"
fi
run bucket allow --read --write --owner "$BUCKET" --key "$KEY_ID" >/dev/null

mkdir -p "$(dirname "$CREDS_FILE")"
cat > "$CREDS_FILE" <<EOF
RM_MINIO_ACCESS_KEY=$KEY_ID
RM_MINIO_SECRET_KEY=$SECRET
RM_MINIO_BUCKET_OTA=$BUCKET
EOF

echo "Garage ready:"
echo "  bucket:     $BUCKET"
echo "  access_key: $KEY_ID"
echo "  credentials saved to $CREDS_FILE"
