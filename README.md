# esp-rainmaker-server

Self-hosted backend implementing the **ESP RainMaker** API contracts (Swagger
specs in `swagger/`) and compatible with both the official ESP firmware
([`espressif/esp-rainmaker`](https://github.com/espressif/esp-rainmaker)) and the
React Native app
([`espressif/esp-rainmaker-home`](https://github.com/espressif/esp-rainmaker-home)).

Designed to be deployed on **Docker Compose (single host)** or **Kubernetes
(multi-host)** — see [Deployment targets](#deployment-targets) below. The
Espressif AWS-managed pieces (Cognito, IoT Core, S3, DynamoDB) are replaced by
self-hosted equivalents:

| AWS component | Self-hosted replacement |
|---|---|
| Cognito (auth) | FastAPI + Postgres-backed JWT |
| IoT Core (MQTT) | VerneMQ + `vmq_webhooks` |
| S3 (OTA / files) | Garage (S3-compatible, Rust) |
| DynamoDB | PostgreSQL 16 + TimescaleDB |
| SQS (jobs) | procrastinate (Postgres-backed queue) |

See **[`docs/REFERENCE.md`](docs/REFERENCE.md)** for the full architecture,
protocol details extracted from the firmware and React Native app, the
TypeScript SDK contract, the Swagger inventory and the TDD scenarios that drive
each phase.

## Architecture

4 application Deployments (single Docker image, different entrypoints) +
3 stateful services:

```
api (FastAPI)              ── handles all HTTP: user REST (JWT), Node API (mTLS), claiming
vmq-authz (FastAPI)        ── VerneMQ webhooks: authn/authz per node certificate
mqtt-ingestor (aiomqtt)    ── subscribes to node/+/+ and persists to Postgres
worker (procrastinate)     ── async jobs: email, OTA rollout, automations cron

vernemq (StatefulSet x3)   ── MQTT broker, mTLS + WSS
postgres (StatefulSet)     ── timescale/timescaledb:latest-pg16
garage (StatefulSet x3)    ── S3-compatible object storage (OTA images, uploads)
```

3 ingress hostnames pointing to the same `api` Service:

- `api.<domain>` — JWT routes (user, admin) for the mobile app
- `claim.<domain>` — `POST /claim/{initiate,verify}` for device claiming
- `node.<domain>` — mTLS-only routes the firmware uses (`/v1/node/*`)

## Quick start (local development)

Requires Python 3.11+, Docker and Docker Compose.

```bash
# 1. Install Python deps in a virtualenv
make install

# 2. Bring up the full stack (postgres+timescale, garage, vernemq, smtp4dev, api, …)
make compose-up

# 3. Apply migrations (also runs automatically in docker-compose, but useful locally)
make migrate

# 4. Run the unit tests
make test

# 5. Iterate on the API with auto-reload
make dev   # uvicorn on http://localhost:8000
```

Sanity check (the dev compose stack puts NGINX in front of `api`, so the
container's port 8000 is not bound to the host — go through NGINX or `exec`
into the container):

```bash
# Through the NGINX Ingress emulator (production-shaped path)
echo "127.0.0.1 api.local claim.local node.local" | sudo tee -a /etc/hosts
curl --cacert var/pki/ca-chain.pem https://api.local/healthz
curl --cacert var/pki/ca-chain.pem https://api.local/v1/apiversions
curl --cacert var/pki/ca-chain.pem https://api.local/v1/mqtt_host

# Or hit the container directly (handy when debugging)
docker compose exec api curl -s localhost:8000/healthz

# In `make dev` mode (uvicorn outside docker), port 8000 is bound:
curl http://localhost:8000/healthz
```

## Project layout

```
app/
├── api/v1/             # FastAPI routers
├── core/               # config, security, errors, logging
├── db/                 # SQLAlchemy session/base
├── models/             # ORM tables
├── schemas/            # pydantic shapes
├── services/           # business logic (auth, nodes, mapping, ota, …)
├── pki/                # CA chain + CSR signing (Phase 2)
├── mqtt/               # subscriber + publisher + topic router (Phase 4)
├── workers/            # procrastinate tasks (Phase 6+)
└── entrypoints/        # 4 process entrypoints: api, vmq_authz, mqtt_ingestor, worker

alembic/                # migrations (sync psycopg)
tests/{unit,integration}/

deploy/
├── docker/Dockerfile   # multi-stage; one image, four CMDs
├── nginx/              # NGINX Ingress emulator (3 vhosts, mTLS on node.*)
├── vernemq/            # broker config (mTLS + webhooks)
├── garage/             # garage.toml
├── compose/            # production overlay + .env template + README
└── k8s/
    ├── base/           # kustomize base (one folder per component)
    └── overlays/{dev,staging,prod}/

scripts/
├── gen_pki.py              # dev PKI (root+intermediate+server certs)
├── init_garage.sh          # one-shot Garage layout + bucket + key
├── fake_node.py            # firmware simulator (claim, MQTT, otafetch)
├── live_verify.sh          # live test cases (Test #2/#7/#9, 21 checks)
└── live_verify_prod_like.sh # NGINX + mTLS + scale + read-only fs (13 checks)

swagger/                # upstream RainMaker OpenAPI specs (read-only)
docs/REFERENCE.md       # consolidated knowledge dump
docs/TEST_PLAN.md       # end-to-end paliers A→F
```

## Configuration

All config is sourced from `RM_*` env vars; see [`.env.example`](.env.example)
(dev) and [`deploy/compose/.env.prod.example`](deploy/compose/.env.prod.example)
(prod-compose) and `app/core/config.py`. In Kubernetes, `ConfigMap
rainmaker-config` holds non-secret values and `Secret rainmaker-*` holds
credentials and PKI material.

## Authentication contract (matches the official TypeScript SDK)

⚠️ Quirks intentionally preserved for SDK compatibility:

- `Authorization: <jwt>` — raw JWT, **no `Bearer` prefix**
- `POST /v1/login2` handles both password login and refresh in one endpoint
- Response keys are **lowercase**: `accesstoken`, `idtoken`, `refreshtoken`
- Error responses are `{"status": "failure", "description": "...", "error_code": <int>}`

The mobile app exposes a runtime config screen (long-press logo) — distribute
a QR code carrying `baseUrl`/`version`/`authUrl` to point it at your server
without forking.

## PKI

Devices authenticate to MQTT and Node API via X.509 mTLS, with the device's
`node_id` as the certificate CN.

The backend uses a **dedicated intermediate CA** signed by an offline root CA
that you provide. The intermediate cert + key + root cert are loaded from the
Kubernetes Secret `rainmaker-pki-ca-intermediate`. The intermediate is what
signs CSRs in the `/claim/verify` flow; the root never lives in the cluster.

## Deployment targets

| Target | Use case | Manifests | Doc |
|---|---|---|---|
| **Docker Compose** (dev) | Local development, smoke tests | `docker-compose.yml` | [`deploy/compose/README.md`](deploy/compose/README.md) |
| **Docker Compose** (prod) | Single-host production (VPS / NAS) — adds restart policies, resource limits, log rotation, Postgres + Garage backups, named TLS cert volumes | `docker-compose.yml` + `deploy/compose/docker-compose.prod.yml` | [`deploy/compose/README.md`](deploy/compose/README.md) |
| **Kubernetes** | Multi-host production, auto-scaling, NetworkPolicies, automated TLS via cert-manager | `deploy/k8s/{base,overlays/*}` | [`deploy/k8s/README.md`](deploy/k8s/README.md) |

Quick start for each is in the linked README. As a rule of thumb:

- ≤ 100 devices + 1 admin → **Compose**
- ≥ 1000 devices, multi-team, need auto-scaling → **Kubernetes**

## Roadmap

Phases 0–8 build the MVP; the TDD scenarios for each phase are documented in
[`docs/REFERENCE.md`](docs/REFERENCE.md). Hors-scope MVP: push notifications,
OAuth tiers, admin / super-admin endpoints, Matter, video streaming.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
