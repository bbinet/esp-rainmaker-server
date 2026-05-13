# esp-rainmaker-server

Self-hosted backend implementing the **ESP RainMaker** API contracts (Swagger
specs in `swagger/`) and compatible with both the official ESP firmware
([`espressif/esp-rainmaker`](https://github.com/espressif/esp-rainmaker)) and the
React Native app
([`espressif/esp-rainmaker-home`](https://github.com/espressif/esp-rainmaker-home)).

Designed to be deployed on Kubernetes, with the Espressif AWS-managed pieces
(Cognito, IoT Core, S3, DynamoDB) replaced by self-hosted equivalents:

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

Sanity check:

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/v1/apiversions
curl http://localhost:8000/v1/mqtt_host
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
├── vernemq/            # broker config (mTLS + webhooks)
├── garage/             # garage.toml
└── k8s/
    ├── base/           # kustomize base (one folder per component)
    └── overlays/{dev,staging,prod}/

swagger/                # upstream RainMaker OpenAPI specs (read-only)
docs/REFERENCE.md       # consolidated knowledge dump
```

## Configuration

All config is sourced from `RM_*` env vars; see [`.env.example`](.env.example)
and `app/core/config.py`. In Kubernetes, `ConfigMap rainmaker-config` holds
non-secret values and `Secret rainmaker-*` holds credentials and PKI material.

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

## Deployment to Kubernetes

```bash
# Render and validate the dev overlay
make k8s-validate

# Apply (after populating real Secrets in your environment)
kubectl apply -k deploy/k8s/overlays/dev
```

cert-manager + Let's Encrypt is assumed for server TLS (`ClusterIssuer` named
`letsencrypt-prod`). The `node.*` Ingress is configured for mTLS verify against
the trust anchor in `rainmaker-pki-ca-intermediate`.

## Roadmap

Phases 0–8 build the MVP; the TDD scenarios for each phase are documented in
[`docs/REFERENCE.md`](docs/REFERENCE.md). Hors-scope MVP: push notifications,
OAuth tiers, admin / super-admin endpoints, Matter, video streaming.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
