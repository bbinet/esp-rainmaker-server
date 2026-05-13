# Kubernetes deployment

Kustomize-based manifests with three environment overlays.

```
deploy/k8s/
├── base/
│   ├── api/                  api Deployment + Service + HPA
│   ├── vmq-authz/            vmq-authz Deployment + Service
│   ├── mqtt-ingestor/        mqtt-ingestor Deployment
│   ├── worker/               procrastinate worker Deployment + HPA
│   ├── postgres/             StatefulSet + headless Service
│   ├── garage/               3-replica StatefulSet
│   ├── vernemq/              3-replica StatefulSet + LoadBalancer
│   ├── ingress.yaml          3 Ingress hostnames (mTLS on node.*)
│   ├── networkpolicies.yaml  default-deny + per-component allow
│   ├── migrations-job.yaml   alembic upgrade head
│   ├── secrets.placeholder.yaml
│   ├── configmap.yaml
│   └── namespace.yaml
└── overlays/
    ├── dev/                  1 replica per Deployment
    ├── staging/              defaults
    └── prod/                 3 replicas for api
```

## Prerequisites

- Kubernetes ≥ 1.28 (tested against 1.30 with kubeconform)
- `kubectl` + Kustomize (built into kubectl 1.21+)
- **cert-manager** with a `ClusterIssuer` named `letsencrypt-prod`
- An **NGINX Ingress Controller** (the manifests use NGINX annotations)
- A working **StorageClass** for the StatefulSets

## Quickstart

```bash
# 1. Validate
make k8s-validate                                # ruff/kubeconform across 3 overlays

# 2. The official image is already published on ghcr.io
#    (.github/workflows/publish.yml builds + pushes on every v*.*.* tag).
#    To pin a specific tag, edit `images:` in deploy/k8s/base/kustomization.yaml:
#
#      images:
#        - name: rainmaker-server
#          newName: ghcr.io/bbinet/esp-rainmaker-server
#          newTag: v0.1.0
#
#    For a fork, build + push to your own ghcr namespace:
#      docker build -t ghcr.io/<your-user>/esp-rainmaker-server:0.1.0 \
#        -f deploy/docker/Dockerfile .
#      docker push ghcr.io/<your-user>/esp-rainmaker-server:0.1.0

# 3. Prepare Secrets (do NOT use the placeholder values)
kubectl create namespace rainmaker

kubectl -n rainmaker create secret generic rainmaker-jwt \
  --from-literal=RM_SECRET_KEY="$(openssl rand -base64 48)"

kubectl -n rainmaker create secret generic rainmaker-db \
  --from-literal=POSTGRES_USER=rainmaker \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 24)" \
  --from-literal=POSTGRES_DB=rainmaker \
  --from-literal=RM_DATABASE_URL='postgresql+asyncpg://rainmaker:...@postgres:5432/rainmaker'

# Your dedicated intermediate CA signed by your offline root
kubectl -n rainmaker create secret generic rainmaker-pki-ca-intermediate \
  --from-file=ca-chain.pem=/secure/path/ca-chain.pem \
  --from-file=ca-intermediate.key=/secure/path/ca-intermediate.key \
  --from-file=ca-root.pem=/secure/path/ca-root.pem

# Garage / SMTP / MQTT internal secrets — see secrets.placeholder.yaml
# for the full list

# 4. Apply
kubectl apply -k deploy/k8s/overlays/prod

# 5. Watch
kubectl -n rainmaker get pods -w
```

## Architecture vs Compose

Identical 7 stateful + 4 stateless services. K8s adds:

| Feature | Why it matters |
|---|---|
| `cert-manager` + Let's Encrypt | Automated cert provisioning + renewal for the 3 Ingress hostnames |
| **mTLS Ingress** on `node.*` | NGINX Ingress Controller validates device certs against the intermediate CA and propagates the CN as `X-SSL-Client-CN` |
| **NetworkPolicies** | `default-deny` at the namespace, then explicit allow paths (e.g. only `api`, `mqtt-ingestor`, `worker` can talk to `postgres:5432`) |
| **HPA** | `api` and `worker` scale on CPU; `mqtt-ingestor` is stable at 1 replica unless you wire a custom metric |
| **Rolling restart** with readiness gates | Zero-downtime upgrades |
| **StatefulSets with PVCs** | Stable identity + persistent storage for `postgres-0`, `garage-{0..2}`, `vernemq-{0..2}` |

## Hostnames

The base Ingress assumes 3 public DNS names (edit
`deploy/k8s/base/ingress.yaml` for your domain):

| Host | TLS | Routes |
|---|---|---|
| `api.rainmaker.example.com` | server-auth | `/v1/user/*`, `/v1/admin/*` |
| `claim.rainmaker.example.com` | server-auth | `/claim/initiate`, `/claim/verify` |
| `node.rainmaker.example.com` | **mTLS** verify client | `/v1/node/*` |

The mTLS verify is configured via:

```yaml
nginx.ingress.kubernetes.io/auth-tls-verify-client: "on"
nginx.ingress.kubernetes.io/auth-tls-secret: "rainmaker/rainmaker-pki-ca-intermediate"
nginx.ingress.kubernetes.io/auth-tls-pass-certificate-to-upstream: "true"
```

VerneMQ is exposed via a separate `Service type: LoadBalancer` on port
8883 (devices) and 8084 (mobile WSS).

## Overlays

| Overlay | Replicas | Resource requests | Suitable for |
|---|---|---|---|
| `dev` | 1 each | minimal | smoke testing in a kind/k3d cluster |
| `staging` | defaults | base requests | pre-prod, share with QA |
| `prod` | api×3, others default | base + HPA | production |

## Validation without a cluster

```bash
make k8s-validate
```

Renders all 3 overlays via `kubectl kustomize` and pipes through
`kubeconform -strict` for schema validation. This is what CI runs.

## Operations

### Backups

The base manifests don't include a backup CronJob — provision via your
cluster's normal mechanism (Velero, CloudNativePG, Stash, etc).

### Rolling upgrade

```bash
# Push a v* tag (the publish.yml workflow handles the image build).
# Then on the cluster:
kubectl -n rainmaker set image deployment/api \
  api=ghcr.io/bbinet/esp-rainmaker-server:v0.2.0
kubectl -n rainmaker rollout status deployment/api
```

The Alembic migration Job runs automatically; if you need to run it
manually before the rollout:

```bash
kubectl -n rainmaker delete job alembic-migrate || true
kubectl -n rainmaker apply -k deploy/k8s/overlays/prod
```

### Debug

```bash
kubectl -n rainmaker logs -f deployment/api
kubectl -n rainmaker exec -it deployment/api -- python -c 'import app; print(app.__version__)'
kubectl -n rainmaker exec -it statefulset/postgres -- psql -U rainmaker rainmaker
```

## Sizing guidance

The base manifests set conservative requests for a small cluster
(3 nodes × 4 vCPU / 8 GB):

| Component | requests.cpu | requests.mem | limits.mem |
|---|---|---|---|
| api | 100m | 256 MB | 512 MB |
| vmq-authz | 50m | 128 MB | 256 MB |
| mqtt-ingestor | 100m | 256 MB | 512 MB |
| worker | 100m | 256 MB | 512 MB |
| postgres | 250m | 512 MB | 2 GB |
| garage | 100m | 256 MB | 1 GB |
| vernemq | 200m | 512 MB | 2 GB |

Adjust via an overlay patch.

## What's not in the manifests

- A monitoring stack — bring your own Prometheus / Grafana / Loki
- A backup CronJob — see above
- HorizontalPodAutoscaler for `mqtt-ingestor` — manual scaling is
  preferred to avoid shared-subscription churn
- PodDisruptionBudgets — add `replicas`-aware PDBs in your overlay

## Choosing between Compose and Kubernetes

| Need | Compose | k8s |
|---|---|---|
| Single host (≤ 100 devices) | ✅ ideal | ❌ over-engineered |
| Multi-host (≥ 1000 devices) | ❌ | ✅ |
| Auto-scaling on metrics | ❌ | ✅ |
| Zero-downtime rolling upgrade | ⚠️ manual | ✅ native |
| Automated TLS provisioning | ⚠️ via Caddy/certbot | ✅ cert-manager |
| Multi-team RBAC + Namespace isolation | ❌ | ✅ |
| `docker compose up` simplicity | ✅ | ❌ |
