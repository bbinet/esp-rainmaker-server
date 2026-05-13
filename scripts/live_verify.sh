#!/usr/bin/env bash
# Live verification scenarios that complement the test suite by
# exercising the running docker-compose stack:
#
#   #2  Security negative tests  (Bearer rejected, missing header, forged
#                                 JWT, cross-user access, revoked cert,
#                                 foreign MQTT topic)
#   #7  Sharing live              (invitation → accept → access → revoke)
#   #9  Automations CRUD live     (+ identifies the missing runtime
#                                 evaluator that is out of MVP scope)
#
# Prerequisites: compose stack up (`docker compose up -d --build`), PKI
# generated (`python scripts/gen_pki.py`), Garage initialised
# (`bash scripts/init_garage.sh`). The Postgres password / RPC secrets
# must match docker-compose.yml.

set -uo pipefail

PASS=0
FAIL=0
RESULT() {
  if [[ "$1" == "$2" ]]; then
    echo "  ✓ PASS — $3"
    PASS=$((PASS+1))
  else
    echo "  ✗ FAIL — $3  (got '$1', expected '$2')"
    FAIL=$((FAIL+1))
  fi
}

# Default to the NGINX Ingress hostname (compose dev exposes `api` only
# behind NGINX since commit 0a7fa04). Override with API=http://localhost:8000
# when running against `make dev` (uvicorn outside docker).
API=${API:-https://api.local}
CA=${CA:-var/pki/ca-chain.pem}

# curl wrapper: when API is https, add --cacert + --resolve so we don't
# depend on /etc/hosts entries; transparent otherwise.
CURL() {
  if [[ "$API" == https://api.local* ]]; then
    curl -s --cacert "$CA" --resolve "api.local:443:127.0.0.1" "$@"
  else
    curl -s "$@"
  fi
}

ensure_user() {
  local name="$1" pwd="$2"
  CURL -X POST "$API/v1/user2" -H 'content-type: application/json' \
    -d "{\"user_name\":\"$name\",\"password\":\"$pwd\"}" >/dev/null
  local code
  code=$(docker compose exec -T postgres psql -U rainmaker -tA \
    -c "SELECT confirm_code FROM users WHERE user_name='$name';" | tr -d ' \n')
  [[ -n "$code" && "$code" != "" ]] && CURL -X PUT "$API/v1/user2" \
    -H 'content-type: application/json' \
    -d "{\"user_name\":\"$name\",\"verification_code\":\"$code\"}" >/dev/null
  CURL -X POST "$API/v1/login2" -H 'content-type: application/json' \
    -d "{\"user_name\":\"$name\",\"password\":\"$pwd\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["accesstoken"])'
}

echo "================ Test #2 — security negative ================"

ALICE_TOKEN=$(ensure_user "alice@live.local" "Alice-Live-1!")
BOB_TOKEN=$(ensure_user "bob@live.local" "Bob-Live-1!")

# T2.1 Bearer prefix
HTTP=$(CURL -o /dev/null -w "%{http_code}" "$API/v1/user2" -H "Authorization: Bearer $ALICE_TOKEN")
RESULT "$HTTP" "401" "T2.1 — Bearer-prefixed token rejected"

# T2.2 Missing header
HTTP=$(CURL -o /dev/null -w "%{http_code}" "$API/v1/user2")
RESULT "$HTTP" "401" "T2.2 — Missing Authorization header rejected"

# T2.3 Forged JWT
HTTP=$(CURL -o /dev/null -w "%{http_code}" "$API/v1/user2" -H "Authorization: eyJ.fake.token")
RESULT "$HTTP" "401" "T2.3 — Forged JWT rejected"

# T2.4 Cross-user access — set up Alice's node + mapping then verify Bob's access denied
ALICE_ID=$(docker compose exec -T postgres psql -U rainmaker -tA \
  -c "SELECT id FROM users WHERE user_name='alice@live.local';" | tr -d ' \n')
NODE_ID="live-alice-node"
docker compose exec -T postgres psql -U rainmaker -c "
INSERT INTO nodes (node_id, registration_ts, online, tags, metadata)
  VALUES ('$NODE_ID', NOW(), false, '[]', '{}')
  ON CONFLICT (node_id) DO NOTHING;
INSERT INTO node_configs (node_id, config_version, payload)
  VALUES ('$NODE_ID', 'v1', '{\"info\":{\"name\":\"AliceBulb\"}}')
  ON CONFLICT (node_id) DO NOTHING;
INSERT INTO user_node_mappings (id, user_id, node_id, role, \"primary\", metadata)
  VALUES (gen_random_uuid(), '$ALICE_ID', '$NODE_ID', 'primary', true, '{}')
  ON CONFLICT (user_id, node_id) DO NOTHING;" >/dev/null

HTTP=$(CURL -o /dev/null -w "%{http_code}" \
  "$API/v1/user/nodes/config?node_id=$NODE_ID" -H "Authorization: $BOB_TOKEN")
RESULT "$HTTP" "403" "T2.4a — Bob can't read Alice's node config"

HTTP=$(CURL -o /dev/null -w "%{http_code}" -X PUT \
  "$API/v1/user/nodes/params?node_id=$NODE_ID" -H "Authorization: $BOB_TOKEN" \
  -H 'content-type: application/json' -d '{"Light":{"power":true}}')
RESULT "$HTTP" "403" "T2.4b — Bob can't set Alice's params"

HTTP=$(CURL -o /dev/null -w "%{http_code}" \
  "$API/v1/user/nodes/config?node_id=$NODE_ID" -H "Authorization: $ALICE_TOKEN")
RESULT "$HTTP" "200" "T2.4c — Alice can read her own node"

# T2.5 / T2.6 — revoked cert and foreign-topic publish via MQTT
if [[ -x scripts/fake_node.py ]] && [[ -d var/pki ]]; then
  .venv/bin/python scripts/fake_node.py provision-key 7CDFA1LIVE01 ESP32S3 >/dev/null 2>&1 || true
  .venv/bin/python scripts/fake_node.py self-claim --mac 7CDFA1LIVE01 --platform ESP32S3 >/dev/null 2>&1 || true
  REV_NODE="7cdfa1live01"

  # Mark cert revoked
  docker compose exec -T postgres psql -U rainmaker -c \
    "UPDATE node_certificates SET revoked=true WHERE node_id='$REV_NODE';" >/dev/null

  RES=$(timeout 5 .venv/bin/python -c "
import asyncio, ssl, sys
import aiomqtt
import paho.mqtt.client as paho
async def main():
    ctx = ssl.create_default_context(cafile='var/pki/ca-chain.pem')
    ctx.load_cert_chain(certfile='var/devices/$REV_NODE/node.pem', keyfile='var/devices/$REV_NODE/node.key')
    try:
        async with aiomqtt.Client('localhost', 8883, identifier='$REV_NODE', tls_context=ctx, protocol=paho.MQTTv311):
            print('connected')
    except Exception:
        print('refused')
asyncio.run(main())
" 2>/dev/null)
  RESULT "$RES" "refused" "T2.5 — Revoked cert refused by VerneMQ + vmq-authz"

  # Un-revoke and try foreign topic
  docker compose exec -T postgres psql -U rainmaker -c \
    "UPDATE node_certificates SET revoked=false WHERE node_id='$REV_NODE';" >/dev/null

  # T2.6: connect, attempt foreign publish, observe vmq-authz refusal in logs.
  # MQTT 3.1.1 QoS 1 doesn't raise on PUBACK-rejection at the aiomqtt layer,
  # so we look at the structured vmq-authz log line that records the decision.
  AUTHZ_BEFORE=$(docker compose logs vmq-authz 2>&1 | grep -c "topic outside namespace" || true)
  timeout 5 .venv/bin/python -c "
import asyncio, ssl
import aiomqtt
import paho.mqtt.client as paho
async def main():
    ctx = ssl.create_default_context(cafile='var/pki/ca-chain.pem')
    ctx.load_cert_chain(certfile='var/devices/$REV_NODE/node.pem', keyfile='var/devices/$REV_NODE/node.key')
    async with aiomqtt.Client('localhost', 8883, identifier='$REV_NODE-pub', tls_context=ctx, protocol=paho.MQTTv311) as c:
        await c.publish('node/other-node/params/local', b'{}', qos=1)
asyncio.run(main())
" 2>/dev/null || true
  sleep 1
  AUTHZ_AFTER=$(docker compose logs vmq-authz 2>&1 | grep -c "topic outside namespace" || true)
  if [[ "$AUTHZ_AFTER" -gt "$AUTHZ_BEFORE" ]]; then
    RESULT "refused" "refused" "T2.6 — Publish to foreign namespace refused by vmq-authz"
  else
    RESULT "no-rejection-log" "refused" "T2.6 — Publish to foreign namespace refused by vmq-authz"
  fi
fi

echo
echo "================ Test #7 — sharing live ================"

# Setup
HTTP=$(CURL -o /dev/null -w "%{http_code}" "$API/v1/user/nodes/config?node_id=$NODE_ID" -H "Authorization: $BOB_TOKEN")
RESULT "$HTTP" "403" "T7.1 — Bob has no access before share"

SHARE=$(CURL -X PUT "$API/v1/user/nodes/sharing/requests" \
  -H "Authorization: $ALICE_TOKEN" -H 'content-type: application/json' \
  -d "{\"nodes\":[\"$NODE_ID\"],\"user_name\":\"bob@live.local\"}")
REQ_ID=$(echo "$SHARE" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("request_id",""))')
[[ -n "$REQ_ID" ]] && RESULT "yes" "yes" "T7.2 — Alice created share request" \
                  || RESULT "no" "yes" "T7.2 — Alice created share request"

ACCEPT=$(CURL -X PUT "$API/v1/user/nodes/sharing/requests" \
  -H "Authorization: $BOB_TOKEN" -H 'content-type: application/json' \
  -d "{\"request_id\":\"$REQ_ID\",\"accept\":true}")
STATUS=$(echo "$ACCEPT" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("request_status",""))')
RESULT "$STATUS" "accepted" "T7.3 — Bob accepts the request"

LIST=$(CURL "$API/v1/user/nodes" -H "Authorization: $BOB_TOKEN")
echo "$LIST" | grep -q "$NODE_ID" && RESULT "yes" "yes" "T7.4 — Bob now sees the node" \
                                 || RESULT "no" "yes" "T7.4 — Bob now sees the node"

HTTP=$(CURL -o /dev/null -w "%{http_code}" "$API/v1/user/nodes/config?node_id=$NODE_ID" -H "Authorization: $BOB_TOKEN")
RESULT "$HTTP" "200" "T7.5 — Bob can read shared config"

HTTP=$(CURL -o /dev/null -w "%{http_code}" -X PUT \
  "$API/v1/user/nodes/params?node_id=$NODE_ID" -H "Authorization: $BOB_TOKEN" \
  -H 'content-type: application/json' -d '{"Light":{"power":true}}')
RESULT "$HTTP" "200" "T7.6 — Bob can write params to shared node"

HTTP=$(CURL -o /dev/null -w "%{http_code}" -X DELETE \
  "$API/v1/user/nodes/sharing?nodes=$NODE_ID&user_name=bob@live.local" \
  -H "Authorization: $ALICE_TOKEN")
RESULT "$HTTP" "200" "T7.7 — Alice revokes the share"

LIST=$(CURL "$API/v1/user/nodes" -H "Authorization: $BOB_TOKEN")
echo "$LIST" | grep -q "$NODE_ID" \
  && RESULT "still-visible" "gone" "T7.8 — Node removed from Bob's listing" \
  || RESULT "gone" "gone" "T7.8 — Node removed from Bob's listing"

HTTP=$(CURL -o /dev/null -w "%{http_code}" "$API/v1/user/nodes/config?node_id=$NODE_ID" -H "Authorization: $BOB_TOKEN")
RESULT "$HTTP" "403" "T7.9 — Bob's access is rescinded immediately"

echo
echo "================ Test #9 — automations CRUD live ================"

AID_RESP=$(CURL -X POST "$API/v1/user/node_automation" \
  -H "Authorization: $ALICE_TOKEN" -H 'content-type: application/json' \
  -d '{
    "name":"Light triggers Fan",
    "event_operator":"and",
    "events":[{"node_id":"'"$NODE_ID"'","device":"Light","param":"power","value":true}],
    "actions":[{"node_id":"'"$NODE_ID"'","device":"Fan","param":"power","value":true}]
  }')
AID=$(echo "$AID_RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("automation_id",""))')
[[ -n "$AID" ]] && RESULT "yes" "yes" "T9.1 — Create automation" \
                || RESULT "no" "yes" "T9.1 — Create automation"

CNT=$(CURL "$API/v1/user/node_automation" -H "Authorization: $ALICE_TOKEN" \
  | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["automations"]))')
[[ "$CNT" -ge 1 ]] && RESULT "ge1" "ge1" "T9.2 — List returns >=1 automation" \
                   || RESULT "0" "ge1" "T9.2 — List returns >=1 automation"

CURL -X PUT "$API/v1/user/node_automation" \
  -H "Authorization: $ALICE_TOKEN" -H 'content-type: application/json' \
  -d "{\"automation_id\":\"$AID\",\"enabled\":false}" >/dev/null
ENABLED=$(CURL "$API/v1/user/node_automation" -H "Authorization: $ALICE_TOKEN" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print([a for a in d['automations'] if a['automation_id']=='$AID'][0]['enabled'])")
RESULT "$ENABLED" "False" "T9.3 — Update enabled=false reflected"

HTTP=$(CURL -o /dev/null -w "%{http_code}" -X DELETE \
  "$API/v1/user/node_automation?automation_id=$AID" -H "Authorization: $ALICE_TOKEN")
RESULT "$HTTP" "200" "T9.4 — Delete automation"

echo
echo "T9.5 — Runtime evaluator gap (informational):"
echo "  The CRUD surface is fully functional. The worker container is a stub —"
echo "  no procrastinate task consumes shadow changes and fires matching actions."
echo "  Wiring this requires Phase 8.5: subscribe to params/local changes (via"
echo "  Postgres LISTEN/NOTIFY or a second MQTT consumer), evaluate the JSONB"
echo "  events, publish actions on params/remote. Out of MVP scope."

echo
echo "================ Summary ================"
echo "  passed: $PASS"
echo "  failed: $FAIL"
[[ "$FAIL" -eq 0 ]] && echo "  ✅ all green" || { echo "  ✗ failures"; exit 1; }
