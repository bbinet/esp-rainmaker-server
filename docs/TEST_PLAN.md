# Plan de test en conditions réelles

Ce document décrit comment valider end-to-end le backend `esp-rainmaker-server`
contre les vrais clients qu'il sert : firmware ESP, appli React Native officielle
et déploiement Kubernetes.

Les tests automatisés (`make test`) couvrent **les contrats internes** (modèles,
services, routes, parsing MQTT) — 78 tests verts au commit `62a5b15`. Ce plan
couvre le reste : la chaîne *réseau + broker + storage + firmware + appli* que
les tests unitaires ne touchent pas.

## Vue d'ensemble — 6 paliers

| Palier | Objectif | Hardware | Statut |
|---|---|---|---|
| **A** | Stack locale auto-vérifiée (curl-only) | aucun | ✅ Validé |
| **B** | Simulateur firmware Python (`scripts/fake_node.py`) | aucun | ✅ Validé (commit `a2060be`) |
| **C** | Appli RN officielle `esp-rainmaker-home` | smartphone | À faire |
| **D** | Vrai device ESP32 flashé `esp-rainmaker` | ESP32-S3/C3/C6/H2 | À faire |
| **E** | Déploiement Kubernetes (kustomize) | cluster kind / k3s / cloud | À faire |
| **F** | Load + chaos (post-MVP) | facultatif | À faire |

---

## Palier A — Stack locale auto-vérifiée

**But** : démarrer toute la pile en local et vérifier que chaque composant
répond.

**Prérequis** : Python 3.11+, Docker + Docker Compose, OpenSSL.

### A.1 PKI test/dev

```bash
make install                        # venv + deps
python scripts/gen_pki.py           # var/pki/{ca-root,ca-intermediate,ca-chain,server}.{pem,key}
openssl verify -CAfile var/pki/ca-root.pem var/pki/ca-intermediate.pem
openssl verify -CAfile var/pki/ca-chain.pem var/pki/server.pem
```

→ doit retourner `OK` deux fois.

### A.2 Démarrer la stack

```bash
docker compose up -d --build
```

Composants : `postgres` (timescaledb-pg16), `garage`, `vernemq`, `smtp4dev`,
`api`, `vmq-authz`, `mqtt-ingestor`, `worker` (+ job `migrate`).

### A.3 Vérifications santé

| Check | Commande | Attendu |
|---|---|---|
| API alive | `curl localhost:8000/healthz` | `{"status":"ok",...}` |
| DB ready | `curl localhost:8000/readyz` | `{"status":"ok","components":{"db":"ok"}}` |
| mqtt_host | `curl localhost:8000/v1/mqtt_host` | broker host:port |
| smtp4dev UI | navigateur → http://localhost:5080 | UI vide |
| VerneMQ HTTP | `curl localhost:8888/metrics` | métriques Prometheus |
| vmq-authz | `curl localhost:8001/healthz` | `{"status":"ok"}` |
| Webhooks enregistrés | `docker compose exec vernemq vmq-admin webhooks show` | 4 hooks listés |
| Logs propres | `docker compose logs api vmq-authz mqtt-ingestor worker` | pas d'exception |

### A.4 Smoke fonctionnel auth

```bash
# Signup
curl -X POST localhost:8000/v1/user2 -H 'content-type: application/json' \
  -d '{"user_name":"alice@local","password":"Alice-Pass-1!"}'

# Récupérer le code (dans smtp4dev OU en BDD)
docker compose exec postgres psql -U rainmaker -c \
  "SELECT user_name, confirm_code FROM users WHERE user_name='alice@local';"

# Confirmer
curl -X PUT localhost:8000/v1/user2 -H 'content-type: application/json' \
  -d '{"user_name":"alice@local","verification_code":"<CODE>"}'

# Login → récupérer accesstoken (lowercase, sans Bearer)
curl -X POST localhost:8000/v1/login2 -H 'content-type: application/json' \
  -d '{"user_name":"alice@local","password":"Alice-Pass-1!"}'

# Accès protégé
curl localhost:8000/v1/user2 -H "Authorization: <accesstoken>"
```

✅ **Critères succès A** : les 4 curl répondent 200, le mail de confirmation
arrive dans smtp4dev, l'API rejette `Authorization: Bearer <token>` (401).

---

## Palier B — Simulateur firmware Python ✅

**But** : reproduire le côté firmware (firmware réel = `esp-rainmaker` en C)
en Python, contre la stack locale du Palier A. Permet de tester *toute* la
chaîne (PKI + claim + MQTT mTLS + ingestor + publisher + shadow) sans
matériel.

**Statut** : implémenté et validé, commit `a2060be`.

### B.1 Bootstrap

```bash
python scripts/gen_pki.py           # déjà fait au Palier A
docker compose up -d --build        # déjà fait au Palier A
```

### B.2 Provisionner une clé HMAC factice (factory-side)

```bash
python scripts/fake_node.py provision-key 7CDFA1000001 ESP32S3
```

Crée une ligne dans `device_provisioning` + sauvegarde la clé en
`var/devices/7cdfa1000001/hmac.key`.

### B.3 Self-claim HMAC

```bash
python scripts/fake_node.py self-claim --mac 7CDFA1000001 --platform ESP32S3
```

Exécute :
1. `POST /claim/initiate` → reçoit `auth_id` + `challenge` (base64)
2. Calcule `HMAC-SHA256(challenge, hmac_key)` → preuve de possession
3. Génère un CSR EC P-256 avec CN = `node_id`
4. `POST /claim/verify` → reçoit le cert PEM signé par notre CA intermédiaire
5. Stocke `node.pem` + `node.key` + `metadata.json` sous `var/devices/<node_id>/`

Vérifier la chaîne :

```bash
openssl verify -CAfile var/pki/ca-chain.pem var/devices/7cdfa1000001/node.pem
openssl x509 -in var/devices/7cdfa1000001/node.pem -noout -subject -issuer
```

→ `subject=CN = 7cdfa1000001`, `issuer=… Intermediate CA`, `OK`.

### B.4 Lancer le device simulé

```bash
python scripts/fake_node.py run --cert-dir var/devices/7cdfa1000001
```

Le simulateur :
- Se connecte à VerneMQ:8883 en mTLS avec son cert
- Publie sa `config` (schéma Lightbulb)
- Publie `params/local/init` (état initial `{Light: {power: false, brightness: 50}}`)
- Souscrit à `params/remote`, `otaurl`, `to-node`
- Toutes les 30s, publie un point `tsdata` (brightness)
- Reçoit `params/remote` et echo sur `params/local`
- Télécharge l'image OTA quand `otaurl` arrive, publie `otastatus`

Vérifications côté backend :

```bash
docker compose exec postgres psql -U rainmaker -c \
  "SELECT node_id, payload->'info'->>'name' FROM node_configs;"

docker compose logs vmq-authz | grep vmq_authz_
```

→ doit montrer `node_configs` peuplé + 1 hook `on_register` + 2 hooks
`on_publish` + 3 hooks `on_subscribe`.

### B.5 Cycle complet user-mapping + params (app ↔ device)

```bash
# Lancer le simulateur en arrière-plan
python scripts/fake_node.py run --cert-dir var/devices/7cdfa1000001 &
SIM_PID=$!

# Côté "app" : signup + login (Palier A déjà fait)
TOKEN=$(curl -s -X POST localhost:8000/v1/login2 \
  -H 'content-type: application/json' \
  -d '{"user_name":"alice@local","password":"Alice-Pass-1!"}' | jq -r .accesstoken)
USER_ID=$(docker compose exec -T postgres psql -U rainmaker -tA \
  -c "SELECT id FROM users WHERE user_name='alice@local';" | tr -d ' \n')

# Mapping legacy secret_key
SECRET="topsecret-$(date +%s)"
curl -X PUT localhost:8000/v1/user/nodes/mapping \
  -H "Authorization: $TOKEN" -H 'content-type: application/json' \
  -d "{\"node_id\":\"7cdfa1000001\",\"secret_key\":\"$SECRET\",\"operation\":\"add\"}"

# Le simulateur publie le secret côté device
python scripts/fake_node.py publish-mapping \
  --cert-dir var/devices/7cdfa1000001 --user-id "$USER_ID" --secret-key "$SECRET"

# Vérifier le mapping
curl localhost:8000/v1/user/nodes -H "Authorization: $TOKEN"
# → {"nodes":["7cdfa1000001"], "total": 1}

# App pousse une valeur
curl -X PUT "localhost:8000/v1/user/nodes/params?node_id=7cdfa1000001" \
  -H "Authorization: $TOKEN" -H 'content-type: application/json' \
  -d '{"Light":{"power":true,"brightness":85}}'

# Le simulateur reçoit params/remote et echo sur params/local
sleep 3

# Vérifier le shadow
docker compose exec postgres psql -U rainmaker -c \
  "SELECT payload FROM node_params_shadow WHERE node_id='7cdfa1000001';"
# → {"Light": {"power": true, "brightness": 85}}

kill $SIM_PID
```

✅ **Critères succès B** : `user_node_mappings` peuplé avec rôle `primary`,
`/v1/user/nodes` retourne le node, le shadow contient la valeur poussée
après le round-trip.

### B.6 Pièges connus

| Problème | Symptôme | Solution |
|---|---|---|
| Permissions PKI 0o600 + container uid 1000 | `Permission denied: ca-intermediate.key` lors du `claim/verify` | Le script `gen_pki.py` crée déjà les clés en 0o644 (PKI de dev seulement) |
| MQTT 5 vs hooks `vmq_webhooks` v3-only | `MqttConnectError: [code:135] Not authorized` | Pin `MQTTv311` dans clients aiomqtt (déjà fait) |
| Subscribe `node/+/+` | Topics 5+ niveaux non livrés (`params/local/init`) | Souscrire à `node/+/#` (déjà corrigé) |
| Multiple certs même CN après re-claim | `MultipleResultsFound` dans vmq-authz | Lookup avec `.limit(1).scalars().first()` (déjà corrigé) |
| `docker compose down -v` efface le PKI volume | Postgres + vernemq repartent vierges | Re-régénérer PKI, re-provisionner, re-claim |

---

## Palier C — Vraie appli RN officielle

**But** : démontrer que l'appli officielle `esp-rainmaker-home` non modifiée
fonctionne contre notre backend self-hosted.

**Prérequis** :
- Smartphone Android ou iOS (ou émulateur)
- Dev machine avec Node 18+, Expo CLI
- Stack Palier A opérationnelle
- Simulateur Palier B opérationnel (pour avoir un node)

### C.1 Cloner et builder l'appli

```bash
git clone https://github.com/espressif/esp-rainmaker-home
cd esp-rainmaker-home
cp .env.example .env
npm install
npx expo start
```

### C.2 Config runtime via QR code

L'appli expose un écran "Runtime Config" caché (tap 10× sur le logo de
l'écran de login). Y rentrer ou scanner un QR :

```json
{
  "baseUrl": "https://<dev-machine-ip>:8000",
  "version": "v1",
  "authUrl": "https://<dev-machine-ip>:8000",
  "clientId": "rainmaker-app"
}
```

⚠ iOS / Android moderne bloquent HTTP en clair — il faut HTTPS. Options :

**Option 1 — mkcert** (recommandé pour dev) :

```bash
brew install mkcert nss
mkcert -install
mkcert localhost <dev-machine-ip> rainmaker.local
# Mettre en place un nginx local devant :8000 avec ces certs
```

**Option 2 — ngrok / cloudflared** : exposer `localhost:8000` derrière
un domaine HTTPS public.

### C.3 Parcours smoke

| # | Action app | Vérification backend |
|---|---|---|
| 1 | Sign up `bob@local / Bob-Pass-1!` | Mail dans smtp4dev (5080) |
| 2 | Saisir code de confirmation | `users.status = confirmed` |
| 3 | Login | `accesstoken` stocké en keychain natif |
| 4 | Page "My Devices" vide | `GET /v1/user/nodes` → `{"nodes":[]}` |
| 5 | curl-mapping du node simulateur vers Bob | `user_node_mappings` peuplé |
| 6 | Pull-to-refresh dans l'app | `GET /v1/user/nodes` → liste contient le node |
| 7 | Tap sur le node → toggle `power` | Simulateur log `recv params/remote` puis `echoed params/local` |
| 8 | Slider `brightness` | Idem, valeur mise à jour dans le shadow |

✅ **Critères succès C** : l'appli affiche le node, les contrôles temps-réel
fonctionnent, le mail de confirmation arrive bien.

### C.4 Pièges connus

| Problème | Solution |
|---|---|
| `Authorization: Bearer <token>` envoyé par erreur | L'appli envoie le JWT brut ; vérifier qu'aucun reverse-proxy ne rewrite le header |
| TLS error sur iOS | mkcert + provisionning profile, ou tester sur Android d'abord |
| `mqtt_host` retourne vide → app fallback HTTP polling | Acceptable pour le smoke ; pour vrai temps-réel, activer le listener WSS de VerneMQ |

---

## Palier D — Vrai device ESP32

**But** : faire fonctionner le **firmware officiel** d'Espressif sur un
device physique contre notre backend.

**Prérequis** :
- ESP32-S3, C3, C6 ou H2 (capables HMAC eFuse pour self-claim) ou ESP32
  classique (assisted-claim uniquement)
- Câble USB, dev machine avec ESP-IDF v5+
- Stack Palier A + appli Palier C opérationnelles

### D.1 ESP-IDF + clone esp-rainmaker

```bash
mkdir -p ~/esp && cd ~/esp
git clone --recursive https://github.com/espressif/esp-idf.git
cd esp-idf && ./install.sh esp32s3 && . ./export.sh
cd ~/esp
git clone --recursive https://github.com/espressif/esp-rainmaker.git
cd esp-rainmaker/examples/led_light
```

### D.2 Pointer le firmware vers notre backend

`idf.py menuconfig` :

```
ESP RainMaker Config →
  Claim Service Base URL : https://<dev-machine-ip>:8000
  Use Self Claiming      : Y   (ou Assisted, pour ESP32 classique)
  MQTT Host              : <dev-machine-ip>
  MQTT Port              : 8883
```

Embed le cert root de notre CA comme trust anchor :

```bash
cp /chemin/vers/esp-rainmaker-server/var/pki/ca-root.pem main/server_cert.pem
```

Ajouter dans `main/CMakeLists.txt` :

```cmake
idf_component_register(
    SRCS "..."
    EMBED_TXTFILES "server_cert.pem"
)
```

### D.3 Self-claim : provisionner la clé HMAC eFuse

⚠ **Brûlage eFuse irréversible**. Vérifier 10× avant d'exécuter.

```bash
# Générer une clé 32 octets
openssl rand -hex 32 > hmac.hex
xxd -r -p hmac.hex hmac.bin

# Burn dans BLOCK_KEY0
espefuse.py burn_key BLOCK_KEY0 hmac.bin HMAC_DOWN_DIGITAL_SIGNATURE

# Provisionner la même clé côté backend
python scripts/fake_node.py provision-key <MAC_HEX> ESP32S3 \
  --key $(cat hmac.hex)
```

Pour **éviter le brûlage eFuse**, utiliser l'**assisted-claim** : pas de clé
HMAC, le téléphone proxie le JWT user pendant le provisioning Wi-Fi.

### D.4 Flash + observation

```bash
idf.py -p /dev/ttyUSB0 flash monitor
```

Logs série attendus :

```
esp_rmaker_claim: Starting claim service ...
esp_rmaker_claim: HMAC challenge received
esp_rmaker_claim: Claim successful, node_id: <id>
esp_rmaker_mqtt: Connected to broker
esp_rmaker_node_config: Config published
esp_rmaker: Params published
```

Côté backend, les logs `vmq-authz` doivent montrer le même CN.

### D.5 Provisioning Wi-Fi + mapping via appli

1. Bouton Reset sur ESP32 → mode provisioning
2. Sur le téléphone, appli `esp-rainmaker-home` → "Add Device"
3. Scanner le QR sur le label du device (encode `node_id` + PoP)
4. L'appli envoie SSID/password + secret_key via BLE/SoftAP
5. Le device se connecte au Wi-Fi, publie sur `node/<id>/user/mapping`
6. L'ingestor matche → mapping créé → l'appli affiche le node

### D.6 Test OTA réel

```bash
# Admin upload binaire
TOKEN=<admin_token>
RESP=$(curl -X POST localhost:8000/v1/admin/otaimage/upload_request \
  -H "Authorization: $TOKEN" -H 'content-type: application/json' \
  -d '{"name":"led_light-1.1.0","fw_version":"1.1.0","file_size":1048576}')

IMG_ID=$(echo $RESP | jq -r .ota_image_id)
URL=$(echo $RESP | jq -r .upload_url)

# Upload du binaire built par idf.py
curl -T build/led_light.bin "$URL"

# Calcul MD5 et confirm
MD5=$(md5sum build/led_light.bin | awk '{print $1}')
curl -X POST localhost:8000/v1/admin/otaimage/upload_confirm \
  -H "Authorization: $TOKEN" -H 'content-type: application/json' \
  -d "{\"ota_image_id\":\"$IMG_ID\",\"file_md5\":\"$MD5\"}"

# Créer le job
curl -X POST localhost:8000/v1/admin/otajob \
  -H "Authorization: $TOKEN" -H 'content-type: application/json' \
  -d "{\"name\":\"prod-rollout\",\"ota_image_id\":\"$IMG_ID\",\"nodes\":[\"<node_id>\"]}"
```

Le device, à son prochain `otafetch` (HTTPS mTLS) ou push MQTT `otaurl`,
télécharge l'image et reboot sur la nouvelle version.

✅ **Critères succès D** : device claim, mappé via appli, contrôlé en temps
réel, OTA déclenché et reboot effectif sur la nouvelle version.

### D.7 Pièges connus

| Problème | Solution |
|---|---|
| Device n'accepte pas notre cert serveur | Vérifier que le `server_cert.pem` embed est bien notre root CA |
| Time skew (`bad cert: not yet valid`) | NTP côté dev machine + device |
| MAC en majuscules dans le firmware vs minuscules en BDD | `node_id` est toujours `mac.lower()` côté backend (déjà géré) |
| OTA URL renvoyée non joignable depuis le device | Si Garage n'est pas accessible publiquement, le device download fail ; en dev, accepter localhost ; en prod, exposer Garage S3 publiquement |

---

## Palier E — Déploiement Kubernetes

**But** : déployer la stack sur un vrai cluster k8s, avec cert-manager,
Ingress mTLS, et NetworkPolicies.

**Prérequis** :
- Cluster k8s (kind / k3s / minikube en local, ou cloud)
- `kubectl` + `kustomize`
- cert-manager installé (`kubectl apply -f https://github.com/cert-manager/cert-manager/releases/...`)
- Domaine DNS résolvant vers le cluster (ou /etc/hosts pour dev)

### E.1 Préparer les Secrets

```bash
# Générer JWT secret
openssl rand -base64 32 > jwt-secret.txt

# Charger PKI : votre intermédiaire signé par votre root offline
# Pour dev : utiliser var/pki/ca-intermediate.{pem,key}

kubectl create namespace rainmaker

kubectl -n rainmaker create secret generic rainmaker-jwt \
  --from-file=RM_SECRET_KEY=jwt-secret.txt

kubectl -n rainmaker create secret generic rainmaker-pki-ca-intermediate \
  --from-file=ca-chain.pem=var/pki/ca-chain.pem \
  --from-file=ca-intermediate.key=var/pki/ca-intermediate.key \
  --from-file=ca-root.pem=var/pki/ca-root.pem

kubectl -n rainmaker create secret generic rainmaker-db \
  --from-literal=POSTGRES_USER=rainmaker \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 24)" \
  --from-literal=POSTGRES_DB=rainmaker \
  --from-literal=RM_DATABASE_URL='postgresql+asyncpg://rainmaker:...@postgres:5432/rainmaker'

# Et ainsi de suite pour garage, smtp, mqtt-internal
```

### E.2 Valider les manifests

```bash
make k8s-validate              # kubectl kustomize sur dev / staging / prod
```

### E.3 Construire et pousser l'image

```bash
docker build -t registry.example.com/rainmaker-server:0.1.0 \
  -f deploy/docker/Dockerfile .
docker push registry.example.com/rainmaker-server:0.1.0

# Mettre à jour le tag dans deploy/k8s/base/kustomization.yaml
```

### E.4 Appliquer l'overlay dev

```bash
kubectl apply -k deploy/k8s/overlays/dev
kubectl -n rainmaker-dev get pods -w
```

Attendre que tous les pods soient `Ready`. Le Job `alembic-migrate` doit
passer en `Completed`.

### E.5 Configurer DNS + cert-manager

```yaml
# ClusterIssuer letsencrypt-prod (ou self-signed pour dev)
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
```

DNS : `api.rainmaker.example.com`, `claim.rainmaker.example.com`,
`node.rainmaker.example.com` doivent pointer vers le LoadBalancer
de l'Ingress.

### E.6 Smoke depuis l'extérieur

```bash
curl https://api.rainmaker.example.com/healthz
curl https://api.rainmaker.example.com/v1/apiversions
```

Tester le claim flow + sim Palier B, mais avec :

```bash
export FAKE_NODE_API=https://claim.rainmaker.example.com
export FAKE_NODE_MQTT_HOST=mqtt.rainmaker.example.com
export FAKE_NODE_MQTT_PORT=8883
python scripts/fake_node.py self-claim --mac 7CDFA1000001 --platform ESP32S3
python scripts/fake_node.py run --cert-dir var/devices/7cdfa1000001
```

Vérifier :
- Ingress `node.rainmaker.example.com` valide bien le cert client (mTLS)
- VerneMQ LoadBalancer accepte la connexion mTLS sur 8883
- NetworkPolicies n'empêchent pas le trafic intra-cluster légitime

### E.7 Backup / restore

Pour valider la résilience :

```bash
# Backup Postgres
kubectl -n rainmaker-dev exec postgres-0 -- pg_dump -U rainmaker rainmaker > backup.sql

# Crash et redémarrage
kubectl -n rainmaker-dev delete pod postgres-0   # le StatefulSet redémarre
# Vérifier que les données sont là (PVC réutilisé)

# Force-restore depuis le backup
kubectl -n rainmaker-dev exec -i postgres-0 -- psql -U rainmaker rainmaker < backup.sql
```

✅ **Critères succès E** : appli RN configurée sur le DNS public se connecte,
device simulé claim et contrôle réussit, certs Let's Encrypt délivrés,
NetworkPolicies appliquées, rolling restart sans perte de données.

### E.8 Pièges connus

| Problème | Solution |
|---|---|
| cert-manager ne délivre pas le cert | Vérifier `ClusterIssuer` + `nginx-ingress` + DNS résolu publiquement |
| Ingress mTLS `node.*` rejette les certs valides | Vérifier `auth-tls-secret` pointe sur le bon Secret avec `ca-chain.pem` |
| VerneMQ LoadBalancer pas joignable | Service `type: LoadBalancer` + cloud provider compatible OU exposer via NodePort |
| Postgres StatefulSet stuck pending | Vérifier la `StorageClass` par défaut du cluster |
| `migrate` Job échoue avec timeout | Augmenter `backoffLimit` ou attendre que Postgres soit `Ready` |

---

## Palier F — Load + chaos (facultatif, post-MVP)

**But** : valider que la stack tient sous charge réaliste et résiste aux
défaillances.

### F.1 Charge devices simultanés

Script : lancer N simulateurs en parallèle.

```bash
for i in $(seq 1 100); do
  MAC=$(printf "7CDFA10%05d" $i)
  python scripts/fake_node.py provision-key $MAC ESP32S3 &
done
wait

for i in $(seq 1 100); do
  MAC=$(printf "7cdfa10%05d" $i)
  python scripts/fake_node.py self-claim --mac ${MAC^^} --platform ESP32S3 &
  python scripts/fake_node.py run --cert-dir var/devices/$MAC &
done
```

Métriques à surveiller :
- VerneMQ `vmq-admin metrics show` : connections, msgs in/out
- Postgres : connexions actives, lag de replication si applicable
- vmq-authz : latence p99 des hooks (doit rester <50ms)
- mqtt-ingestor : backlog de messages

### F.2 Chaos VerneMQ

```bash
# Kill une réplique de vernemq
kubectl -n rainmaker delete pod vernemq-1
# Les devices se reconnectent automatiquement
# Vérifier que les messages en flight QoS 1 sont rejoués
```

### F.3 Chaos Postgres

```bash
# Kill primary postgres
kubectl -n rainmaker delete pod postgres-0
# api / mqtt-ingestor doivent reconnecter
# Aucune perte de données (PVC + WAL)
```

### F.4 Chaos Garage

```bash
# Stopper 1 des 3 répliques garage
# Les uploads OTA doivent encore fonctionner (réplication 2/3)
```

✅ **Critères succès F** : >1000 devices connectés simultanément avec
latence p99 <500ms sur params/remote round-trip, rolling restart des
stateful sans interruption visible côté appli.

---

## Récapitulatif des outils

| Outil | Usage |
|---|---|
| `make install` | Crée venv + installe dépendances |
| `make test` | 78 tests unit + intégration |
| `make compose-up` | Lance la stack locale |
| `python scripts/gen_pki.py` | Génère la PKI dev (root + intermédiaire + serveur) |
| `python scripts/fake_node.py <cmd>` | Simulateur firmware (provision/claim/run/mapping) |
| `make k8s-validate` | Valide les overlays kustomize |
| `docker compose logs vmq-authz \| grep vmq_authz_` | Trace les décisions d'autorisation |
| `docker compose exec postgres psql -U rainmaker` | Inspection BDD |
| `openssl verify -CAfile var/pki/ca-chain.pem <cert>` | Validation chaîne PKI |

## Hors-scope explicite

- **Push notifications** (Phase 9) — pas testé tant que pas implémenté
- **OAuth tiers Google/Apple** — pas dans le MVP
- **Matter** — pas dans le scope
- **Performance >10 000 devices** — Palier F couvre jusqu'à 1k, au-delà
  prévoir partitioning des ingestors via shared subscriptions VerneMQ
