# Docker Compose deployment

Two flavours:

| File | Use case |
|---|---|
| `docker-compose.yml` (at repo root) | **Dev / single-host smoke** — bind-mounts `var/pki/`, exposes container ports for tooling, `RM_DEBUG=true`, no restart policies. |
| `deploy/compose/docker-compose.prod.yml` (overlay) | **Production single-host** — adds restart policies, resource limits, log rotation, replicas, Postgres + Garage backups, named volumes for PKI and TLS certs. |

Both stacks expose the same architecture:

```
        ┌─────────────────┐
        │     NGINX        │  3 vhosts (api/claim/node), mTLS verify on node.*
        └────────┬────────┘
                 │ HTTP
        ┌────────┴────────┐
        │    api ×N        │  FastAPI; serves all HTTP routes
        ├──────────────────┤
        │   vmq-authz ×N   │  VerneMQ webhook decisions
        ├──────────────────┤
        │ mqtt-ingestor ×N │  shared-subscription consumers
        ├──────────────────┤
        │   worker         │  procrastinate background tasks
        └──────────────────┘
                 │
        ┌────────┴────────┐
        │  postgres+ts    │  TimescaleDB (StatefulSet equivalent)
        ├──────────────────┤
        │     garage       │  S3-compatible object storage
        ├──────────────────┤
        │    vernemq       │  MQTT broker (mTLS + WSS)
        └──────────────────┘
```

---

## Dev quickstart

```bash
# 1. Generate test PKI artifacts
python scripts/gen_pki.py

# 2. Bring up the stack
# The 5 third-party images are pulled from ghcr.io/bbinet/ (mirrored
# by .github/workflows/mirror.yml) to avoid Docker Hub rate-limits.
# To pin a different source, set RM_IMAGE_<NAME> (see docker-compose.yml).
docker compose up -d --build

# 3. Initialise Garage (one-shot)
bash scripts/init_garage.sh

# 4. Tell `api` to use the real S3 backend
cat var/garage-credentials.env >> .env
echo "RM_FEATURE_S3_REAL=true" >> .env
echo "RM_MINIO_PUBLIC_ENDPOINT=localhost:3900" >> .env
docker compose --env-file .env up -d api

# 5. Smoke-test
echo "127.0.0.1 api.local claim.local node.local" | sudo tee -a /etc/hosts
bash scripts/live_verify.sh             # → 21/21
bash scripts/live_verify_prod_like.sh   # → 13/13
```

Endpoints in dev:
- `https://api.local`        — user / app REST (JWT)
- `https://claim.local:444`  — device claiming
- `https://node.local:445`   — node mTLS API
- `localhost:8883`           — MQTT mTLS (devices)
- `localhost:5080`           — smtp4dev UI

---

## Production deployment

Designed for a **single VPS / NAS / Raspberry Pi-class host** running
Docker. For multi-host / multi-region, use the Kubernetes target
(`deploy/k8s/`).

### Prerequisites

- Docker 24+ with Compose v2
- A public DNS name with A/AAAA records pointing at your host:
    - `api.example.com`
    - `claim.example.com`
    - `node.example.com`
    - `mqtt.example.com` (4-th hostname for the broker)
- TLS certs for those hostnames (Let's Encrypt or commercial)
- An **intermediate CA cert + key dedicated to RainMaker devices**,
  signed by your offline root CA (see "PKI bootstrap" below)

### 1. Bootstrap the host

```bash
# Clone
git clone https://github.com/bbinet/esp-rainmaker-server.git
cd esp-rainmaker-server

# Prepare prod env file
cp deploy/compose/.env.prod.example deploy/compose/.env.prod
# Edit deploy/compose/.env.prod with real secrets

# Generate or copy in your PKI artifacts
# Option A: dev-style (NOT recommended for prod) — generates self-signed root
python scripts/gen_pki.py

# Option B: bring your own intermediate signed by your offline root
mkdir -p var/pki
cp /secure/path/ca-root.pem         var/pki/ca-root.pem
cp /secure/path/ca-intermediate.pem var/pki/ca-intermediate.pem
cp /secure/path/ca-intermediate.key var/pki/ca-intermediate.key
chmod 644 var/pki/ca-intermediate.key
cat var/pki/ca-intermediate.pem var/pki/ca-root.pem > var/pki/ca-chain.pem
# Generate the NGINX server cert against your real DNS names — see
# scripts/gen_pki.py for the template; adjust the SANs.
```

### 2. Start the stack

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  --env-file deploy/compose/.env.prod \
  up -d
```

### 3. Initialise Garage (one-shot)

```bash
bash scripts/init_garage.sh
# Copy the resulting credentials into deploy/compose/.env.prod
# (RM_MINIO_ACCESS_KEY, RM_MINIO_SECRET_KEY)
# Then re-up:
docker compose -f ... -f ... --env-file deploy/compose/.env.prod up -d api
```

### 4. Provision TLS for NGINX

Three options, ordered by complexity:

**Option 1 — Bring-your-own certs (simplest)**

Put your Let's Encrypt / commercial certs into the `nginx_certs` named
volume and reload NGINX. Example with a temporary helper container:

```bash
docker run --rm -v rainmaker_nginx_certs:/dst -v /etc/letsencrypt/live/api.example.com:/src:ro alpine \
  sh -c 'cp /src/fullchain.pem /dst/nginx-chain.pem && cp /src/privkey.pem /dst/nginx.key'

docker compose -f docker-compose.yml -f deploy/compose/docker-compose.prod.yml \
  --env-file deploy/compose/.env.prod kill -s HUP nginx
```

For per-host SAN coverage either issue a single multi-SAN cert covering
all 3 hostnames, or run NGINX with 3 separate cert/key pairs and use
SNI-based selection.

**Option 2 — Certbot sidecar**

Run a one-shot certbot container against the public DNS names, then
copy the renewed certs into the `nginx_certs` volume on a cron schedule.
Sample script:

```bash
docker run --rm \
  -v rainmaker_nginx_certs:/etc/letsencrypt \
  -p 80:80 certbot/certbot certonly \
  --standalone --agree-tos --no-eff-email -m ops@example.com \
  -d api.example.com -d claim.example.com -d node.example.com
```

NGINX picks up the new cert automatically on the next `nginx -s reload`
or container restart.

**Option 3 — Switch the reverse proxy to Caddy or Traefik**

Both auto-renew via Let's Encrypt with zero config. To swap NGINX for
Caddy, drop the `nginx` service and add:

```yaml
caddy:
  image: caddy:2-alpine
  restart: unless-stopped
  ports: ["443:443", "80:80"]
  volumes:
    - ./deploy/compose/Caddyfile:/etc/caddy/Caddyfile:ro
    - caddy_data:/data
    - caddy_config:/config
volumes:
  caddy_data:
  caddy_config:
```

with a `Caddyfile` like:

```caddyfile
api.example.com    { reverse_proxy api:8000 }
claim.example.com  { reverse_proxy api:8000 }
node.example.com {
    tls /path/to/cert /path/to/key {
        client_auth {
            mode     require_and_verify
            trust_pool file /etc/rainmaker/pki/ca-chain.pem
        }
    }
    reverse_proxy api:8000 {
        header_up X-SSL-Client-CN {http.request.tls.client.subject_cn}
    }
}
```

### 5. Verify

```bash
curl https://api.example.com/healthz
curl https://api.example.com/v1/apiversions
```

### Operations

#### Backups

Postgres dumps land in the `pg_backups` volume:

```bash
docker run --rm -v rainmaker_pg_backups:/backups alpine ls -la /backups/daily
# → daily/rainmaker-2026-05-13.sql.gz
```

Restore:

```bash
docker run --rm -i \
  --network rainmaker_default \
  -v rainmaker_pg_backups:/backups \
  postgres:16 sh -c 'gunzip -c /backups/daily/rainmaker-2026-05-13.sql.gz | psql -h postgres -U rainmaker rainmaker'
```

Garage snapshots in `garage_backups` follow the same pattern (rclone
copies of the OTA bucket to `garage_backups/<timestamp>/`).

#### Scaling

```bash
docker compose -f docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  --env-file deploy/compose/.env.prod \
  up -d --scale api=4 --scale mqtt-ingestor=3
```

NGINX load-balances `api` automatically; `mqtt-ingestor` replicas use
shared subscription so messages aren't duplicated.

#### Logs

```bash
docker compose logs -f api          # tail one service
docker compose logs --since=1h      # all services last hour
```

Logs are JSON-formatted (`RM_LOG_JSON=true`) with structlog;
`json-file` driver rotates at 10 MB × 5 files per container.

#### Upgrade

```bash
git pull
docker compose -f docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  --env-file deploy/compose/.env.prod \
  up -d --build
```

The Alembic migration job runs automatically before the api/worker
deployments come online (`depends_on: migrate condition: completed`).

### Sizing guidance

The prod overlay sets soft limits sized for a 4 vCPU / 8 GB host:

| Service | CPU limit | Mem limit | Replicas |
|---|---|---|---|
| postgres | 2 | 2 GB | 1 (singleton) |
| garage | 1 | 1 GB | 1 |
| vernemq | 2 | 1 GB | 1 |
| api | 1 each | 512 MB each | 2 |
| vmq-authz | 0.5 each | 256 MB each | 2 |
| mqtt-ingestor | 1 each | 512 MB each | 2 |
| worker | 1 | 512 MB | 1 |
| nginx | 1 | 256 MB | 1 |

Bump these in your own override file as needed.

### What's not covered by Compose Prod

- **Multi-host distribution** — single Docker daemon, no scheduling
- **NetworkPolicies** — equivalent isolation only via separate networks
- **HPA-style auto-scaling** — manual `--scale` only

For any of these, use the **Kubernetes target** (`deploy/k8s/`).
