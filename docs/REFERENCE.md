# Référence technique — Backend self-hosted ESP RainMaker

> Document de référence consolidé regroupant tout ce qui a été appris des fichiers
> Swagger, du firmware open-source `esp-rainmaker` et de l'appli React Native
> `esp-rainmaker-home`. Sert de base de connaissance si une session de dev est
> interrompue et qu'il faut reprendre depuis zéro.

---

## 1. Contexte général

ESP RainMaker est la solution end-to-end d'Espressif pour le contrôle distant de
devices basés sur ESP32 sans config cloud côté utilisateur. Le service de référence
est hébergé sur AWS (Cognito, IoT Core, S3, DynamoDB). Ce projet construit une
**alternative auto-hébergeable**, déployable sur **Docker Compose (single
host)** ou **Kubernetes (multi-host)** — voir `deploy/compose/README.md` et
`deploy/k8s/README.md`. Compatible avec :

1. Le **firmware ESP** : https://github.com/espressif/esp-rainmaker
2. L'**appli RN** (Expo, React Native) : https://github.com/espressif/esp-rainmaker-home
3. Les **Swaggers** dans `swagger/Rainmaker_*.yml|yaml` (~35 000 lignes, ~100 endpoints)

### Cibles
- **Trois acteurs distincts** parlent au backend :
  - Le **firmware ESP** (MQTT mTLS + HTTPS Node API mTLS + service de claiming HTTPS)
  - L'**appli RN** (HTTPS REST avec JWT + MQTT/WSS optionnel)
  - L'**admin / super-admin** (HTTPS REST avec JWT + rôle) — hors MVP

---

## 2. Architecture cible

Mêmes 7 services dans les deux targets ; les différences sont uniquement
**opérationnelles** (réplication, autoscaling, isolation réseau). La
description ci-dessous utilise le vocabulaire k8s ; pour la traduction
Compose, voir `deploy/compose/README.md`.

### Composants applicatifs (image Docker unique, 4 Deployments)

| Composant | Type k8s | Rôle |
|---|---|---|
| `api` | Deployment + HPA | Tous les routes HTTP : user REST (JWT), Node API mTLS, claiming. Différenciation par hostname Ingress + dépendances FastAPI. |
| `vmq-authz` | Deployment léger | Webhooks VerneMQ (`auth_on_register`, `auth_on_publish`, `auth_on_subscribe`, `on_client_online`, `on_client_offline`). Latence garantie indépendante de la charge HTTP. |
| `mqtt-ingestor` | Deployment | Subscribe `node/+/+` avec credentials backend. Routage par topic → persistance Postgres (config, shadow, ts, mapping match, ota status). |
| `worker` | Deployment + HPA | procrastinate workers (email, OTA rollout, automations cron). Broker = Postgres. |

### Composants infra

| Composant | Type k8s | Image |
|---|---|---|
| `vernemq` | StatefulSet 3 répliques | `vernemq/vernemq:latest` avec plugin `vmq_webhooks` |
| `postgres` | StatefulSet + PVC | `timescale/timescaledb:latest-pg16` |
| `garage` | StatefulSet 3 répliques | `dxflrs/garage` (~30 MB, S3-compatible, Rust) |

### Ingress

- 3 hostnames pointent vers le **même Service `api`** :
  - `api.<domain>` → routes JWT (pas de mTLS Ingress)
  - `claim.<domain>` → routes claim (TLS serveur uniquement)
  - `node.<domain>` → routes node mTLS (`auth-tls-verify-client: on`, `auth-tls-secret` = CA root publique, propagation `X-SSL-Client-CN`)
- cert-manager + Let's Encrypt pour les certs serveur

### Sécurité

- **Pod Security `restricted`** : runAsNonRoot, readOnlyRootFilesystem (+ emptyDir `/tmp`), seccomp `RuntimeDefault`
- **NetworkPolicies** default-deny + allow ciblés
- **Secrets k8s** : `jwt-secret`, `pki-ca-intermediate` (clé privée + cert intermédiaire + cert root pour chaîne complète), `db-credentials`, `garage-credentials`, `vmq-internal-credentials`, `smtp-credentials`

### PKI

- **CA intermédiaire dédiée RainMaker** signée par la CA root utilisateur (offline)
- Clé + cert intermédiaire en Secret k8s, chargés en mémoire au démarrage de `api`
- La CA root reste hors-cluster ; son cert public est aussi monté pour configurer les trust anchors VerneMQ + Ingress mTLS
- Rotation : nouveau CSR intermédiaire → signature par root offline → mise à jour Secret → rolling restart

---

## 3. Décisions techniques verrouillées

| Choix | Valeur |
|---|---|
| Langage / framework | Python 3.11+ / FastAPI / uvicorn |
| ORM / migrations | SQLAlchemy 2 async / Alembic |
| Base | PostgreSQL 16 + extension TimescaleDB |
| Queue / scheduling | procrastinate (broker Postgres, **pas de Redis**) |
| Pub/sub temps réel | LISTEN/NOTIFY Postgres |
| Rate-limit & cache | tables Postgres avec TTL applicatif |
| Stockage objet | Garage (S3-compatible) |
| Broker MQTT | VerneMQ + plugin `vmq_webhooks` |
| Auth utilisateur | JWT HS256 brut (`Authorization: <token>` **sans `Bearer`**), email/password MVP |
| PKI device | CA intermédiaire dédiée chargée en Secret k8s |
| Certs TLS serveur | cert-manager + Let's Encrypt |
| Packaging k8s | Kustomize (base + overlays dev/staging/prod) |
| Observabilité MVP | Logs JSON structlog + `/metrics` Prometheus + `/healthz` / `/readyz` |
| Méthode dev | TDD (skill `tdd`) avec scénarios end-to-end firmware + app |

**Hors-scope MVP** : Redis, OAuth tiers, push notifs, admin/super-admin, Matter, secure_sign, passthrough, video/assume_role, console admin.

---

## 4. Firmware ESP — protocole device → backend

Sources : `github.com/espressif/esp-rainmaker`, composants `components/esp_rainmaker/src/core/`.

### 4.1 Claiming / provisioning

Un device fraîchement flashé n'a pas de certificat. Il en obtient un via un service
HTTPS de claiming (host séparé du broker MQTT et de l'API Node). URL de référence :
`https://esp-claiming.rainmaker.espressif.com`.

**Deux modes** :

| Mode | Quand | Auth |
|---|---|---|
| **Self-claim** (`ESP_RMAKER_SELF_CLAIM`) | Devices avec clé HMAC eFuse (ESP32-S2/S3/C3/C6/H2) | Challenge-response HMAC, pas de token utilisateur |
| **Assisted claim** (`ESP_RMAKER_ASSISTED_CLAIM`) | Devices sans clé eFuse (ESP32 classique) | JWT utilisateur fourni par le téléphone via provisioning |

**Endpoint `POST /claim/initiate`**
- Auth : `Authorization: <access_token>` (assisted) ou absent (self-claim)
- Body : `{"mac_addr": "7CDFA100033B", "platform": "ESP32S3"}`
- Réponse assisted : `{"node_id": "..."}`
- Réponse self-claim : `{"auth_id": "...", "challenge": "<≤128B hex>"}`

**Endpoint `POST /claim/verify`**
- Body self-claim : `{"auth_id": "...", "challenge_response": "<HMAC-SHA256 hex>", "csr": "<PEM>", "send_mqtt_host": true}`
- Body assisted : `{"csr": "<PEM>", "send_mqtt_host": true, "node_policies": "mqtt,videostream"}`
- Réponse : `{"certificate": "<PEM>", "mqtt_host": "...", "mqtt_cred_host": "..."}`

Le device stocke cert + key en NVS et utilise **mTLS** ensuite pour MQTT et HTTPS Node API.

**Transport assisted-claim** : pendant le provisioning Wi-Fi (SoftAP/BLE), le téléphone parle au device via **protocomm** sur endpoint `rmaker_claim` (protobuf : `CmdClaimStart`, `CmdClaimInit`, `CmdClaimVerify`, `CmdClaimAbort`). Le téléphone fait les appels HTTP `/claim/*` et relaie via protocomm.

### 4.2 MQTT topics

Tous préfixés `node/<node_id>/`. Strings depuis `components/esp_rainmaker/src/core/esp_rmaker_mqtt_topics.h`.

| Direction | Topic | Topic-rule alias | Purpose |
|---|---|---|---|
| Pub (device→cloud) | `config` | `esp_node_config` | Schéma devices/params/services |
| Pub | `params/local` | `esp_set_params` | Reported state (current values) |
| Pub | `params/local/init` | `esp_init_params` | Initial values au boot |
| Sub (cloud→device) | `params/remote` | — | Desired state / commands |
| Pub | `alert` | `esp_node_alert` | Push alerts |
| Pub | `user/mapping` | `esp_user_node_mapping` | User claim (cf §4.5) |
| Pub | `otafetch` | `esp_node_otafetch` | OTA poll (MQTT fallback) |
| Sub | `otaurl` | — | OTA job pushé par cloud |
| Pub | `otastatus` | `esp_node_otastatus` | OTA progress |
| Pub | `tsdata` | `esp_ts_ingest` | Time-series data |
| Pub | `simple_tsdata` | `esp_simple_ts_ingest` | Simple time-series |
| Pub | `from-node` | `esp_cmd_resp` | Command-response (req from cloud, reply from node) |
| Sub | `to-node` | — | Cloud → node command |
| Pub | `diagnostics/from-node` | — | ESP-Insights diagnostics |

QoS 1 partout (PUBACK requis pour la state machine user-mapping).

### 4.3 Authentification device

- **MQTT** : TLS 1.2 mTLS avec cert/key obtenu via `/claim/verify`. Port 8883.
- **HTTPS Node API** (`api.node.rainmaker.espressif.com`) : même cert mTLS. Le swagger précise *"Requires MutualTLS based authentication using the node certificate and key"*.
- **Download OTA presigned URL** : plain HTTPS (protégé par signature dans l'URL).

### 4.4 OTA flow

**Deux chemins** :

**Pushed (MQTT)** : cloud publie `node/<id>/otaurl` payload :
```json
{"ota_job_id":"...", "url":"https://<s3-presigned>", "file_size":2097152,
 "fw_version":"1.2.3", "file_md5":"<hex>"}
```

**Polled (HTTPS mTLS)** : `GET https://api.node.rainmaker.espressif.com/v1/node/otafetch`. Réponse :
```json
{"url":"https://presigned/firmware.bin", "ota_job_id":"...",
 "fw_version":"1.0.0", "file_md5":"5d41402abc4b2a76b9719d911017c592",
 "file_size":2097152, "metadata":{}, "stream_id":"..."}
```
Forme "wait" : `{"ota_available":true, "action":"wait", "min_wait":30, "max_wait":300, "ota_task":3600}`.

**Download** : HTTPS GET sur `url`. Vérif MD5.

**Status report** : MQTT publish `otastatus` ou `POST /v1/node/otastatus` mTLS :
```json
{"ota_job_id":"...", "status":"in-progress|success|failed|rejected|delayed",
 "additional_info":"...", "ts":1648147200}
```

### 4.5 User-node mapping (QR / secret-key flow)

Objectif : lier un node fraîchement claim à un compte utilisateur.

```
1. App génère secret_key S aléatoire
2. App envoie {user_id, S} au device via protocomm endpoint `cloud_user_assoc`
   (protobuf CmdSetUserMapping) pendant le provisioning Wi-Fi
3. App appelle PUT /v1/user/nodes/mapping {node_id, secret_key:S}
4. Device, une fois MQTT actif, publie sur node/<id>/user/mapping :
   {"node_id":"<id>","user_id":"<uid>","secret_key":"<S>","reset":false}
5. Cloud match (node_id, secret_key) → persiste binding → PUBACK
6. Device marque DONE en NVS, ne republiera plus (sauf reset:true)
```

Le QR code sur le label device encode `node_id`, MAC, PoP (proof-of-possession) — pas le secret de mapping. Le secret est généré par le téléphone au moment du claim.

### 4.6 Local control (LAN)

Composant : `esp_rmaker_local_ctrl.c` utilisant `esp_local_ctrl` IDF.

- Transport : plain HTTP sur TCP port (default 8080), protocomm-over-HTTPD
- Discovery : mDNS service type `_esp_local_ctrl._tcp`, instance = `node_id`. Sur Thread, SRP.
- TXT record `pop_required=yes`
- Auth : protocomm security v1 (SRP6a-style seeded par 9-byte hex PoP en NVS)
- Endpoints protocomm : `config` (GET), `params` (GET/SET), optionnel `ch_resp`

**Aucun round-trip cloud** — fonctionne sans internet quand téléphone et device sont sur le même LAN. **Aucune action côté backend.**

### 4.7 Node config (schema devices/params/services)

Publié sur `node/<id>/config`, retourné aussi par `GET /v1/node/config` :

```json
{
  "node_id": "abcd1234",
  "config_version": "2019-09-11",
  "info": {"name":"My_Light", "fw_version":"2.0", "type":"LightBulb",
           "model":"...", "project_name":"esp-bulb", "platform":"esp32s3"},
  "attributes": [{"name":"serial_number", "value":"012345"}],
  "devices": [{
    "name":"Light", "type":"esp.device.lightbulb", "primary":"power",
    "attributes":[{"name":"serial_number","value":"012345"}],
    "params":[
      {"name":"power", "type":"esp.param.power",
       "data_type":"bool", "properties":["read","write"], "ui_type":"esp.ui.toggle"},
      {"name":"brightness", "type":"esp.param.brightness",
       "data_type":"int", "properties":["read","write","time_series"],
       "bounds":{"min":0,"max":100,"step":1}, "ui_type":"esp.ui.slider"}
    ]
  }],
  "services": [{
    "name":"Time", "type":"esp.service.time",
    "params":[{"name":"TZ","type":"esp.param.tz","data_type":"string","properties":["read","write"]}]
  }]
}
```

`properties` ∈ `read | write | time_series`. Services standards : `esp.service.time`, `esp.service.ota`, `esp.service.schedule`, `esp.service.scenes`, `esp.service.system`, `esp.service.local_control`.

### 4.8 Params payload (shadow-like state)

Sur `params/local` (device→cloud) ET `params/remote` (cloud→device) :

```json
{
  "Light": {"power": true, "brightness": 65},
  "Time": {"TZ": "Asia/Kolkata"}
}
```

Clés = device ou service `name`, sous-clés = param `name`. Sur `params/remote`, seuls les params changés peuvent apparaître ; le device merge dans son shadow.

### 4.9 Alert

```json
{"esp.alert.str": "Filter needs cleaning"}
```

### 4.10 Time-series

`tsdata` :
```json
{
  "ts_data_version": "2021-09-13",
  "ts_data": [{
    "name":"Light.brightness", "type":"esp.param.brightness", "dt":"int",
    "t": 1715600000,
    "records":[{"t":1715600000,"v":65},{"t":1715600030,"v":70}]
  }]
}
```

`simple_tsdata` :
```json
{"name":"Light.brightness", "type":"esp.param.brightness",
 "dt":"int", "t":1715600000, "v":65, "d":30}
```

### 4.11 Fichiers de référence (upstream firmware)
- `components/esp_rainmaker/src/core/esp_rmaker_mqtt_topics.h` — topic names
- `components/esp_rainmaker/src/core/esp_rmaker_claim.{c,h}`
- `components/esp_rainmaker/src/core/esp_rmaker_user_mapping.c`
- `components/esp_rainmaker/src/core/esp_rmaker_local_ctrl.c`
- `components/esp_rainmaker/src/core/esp_rmaker_param.c`
- `components/esp_rainmaker/src/core/esp_rmaker_node_config.c`

---

## 5. Appli RN `esp-rainmaker-home` — contrat backend

Sources : `github.com/espressif/esp-rainmaker-home` (React Native + Expo).

### 5.1 Architecture de l'appli

- **React Native (Expo)** — pas natif Android pur ; le code Kotlin/Swift sous `android/` et `ios/` est de la glue (OAuth browser, BLE/SoftAP provisioning, mDNS, push)
- **Toute la logique HTTP/MQTT** vit dans 3 SDK npm :
  - `@espressif/rainmaker-base-sdk` (3.0.0) → `espressif/esp-rainmaker-app-sdk-ts` — **contrat canonique** de l'API REST classique. **C'est lui qu'on implémente.**
  - `@espressif/rmng-base-sdk` (1.1.0) — backend "RainMaker NG" (API Gateway AWS), uniquement si `ACTIVE_SDK=rmng-sdk`. Hors scope.
  - `@espressif/rainmaker-matter-sdk` (2.0.0) — wrap base + Matter
- Default `ACTIVE_SDK=rainmaker-matter-sdk` qui s'appuie sur le base SDK → contrat classique à respecter.

### 5.2 Base URL & version (configurables)

Depuis `.env.example` et `config/sdk.config.ts` :
```
BASE_URL=https://api.rainmaker.espressif.com
API_VERSION=v1
```

URL build dans `src/services/ESPRMAPIManager.ts` : `${baseUrl}/${version}/${endpointPath}`.

Tout appel REST classique = `https://<host>/v1/<path>`.

**Important** : un écran "RuntimeConfigManager" (tap logo 10× sur écran login) permet d'override `baseUrl`, `version`, `authUrl`, OAuth `clientId` via un QR code. **Pas besoin de forker l'appli** — distribuer un QR de config suffit.

Autres URLs hardcodées :
- Claim service : `https://esp-claiming.rainmaker.espressif.com` (`DEFAULT_CLAIM_BASE_URL` dans `src/utils/constants.ts`)

### 5.3 Authentification

- **Login user/pass** : `POST /v1/login2` body `{"user_name": "...", "password": "..."}`. Réponse `{accesstoken, idtoken, refreshtoken}` **en minuscules sans underscore**.
- **Refresh** : également `POST /v1/login2` body `{"refreshtoken": "<token>"}`. Pas de `/extendsession` séparé.
- **OAuth code grant** (hors scope MVP) : `${authUrl}/authorize` puis form POST `${authUrl}/token`.
- **Autres endpoints auth** dans `APIEndpoints` :
  - `POST /v1/user2` — sign-up code
  - `PUT /v1/user2` — confirm sign-up
  - `GET /v1/user2` — current user
  - `PUT /v1/user2` — update user
  - `DELETE /v1/user2` — delete account
  - `PUT /v1/forgotpassword2` — request reset
  - `PUT /v1/password2` — set new password
  - `POST /v1/logout2`
  - `POST /v1/login2` avec OTP body — request/validate login OTP (même endpoint, body différent)

**Header Authorization** : `Authorization: <accessToken>` — **JWT brut, sans `Bearer` !** Un 401 déclenche clear + ré-login (le SDK utilise un interceptor qui tente un refresh silencieux).

- HTTP client = **fetch** (pas axios)
- Token storage keys : `com.esprmbase.accessToken`, `com.esprmbase.idToken`, `com.esprmbase.refreshToken`

### 5.4 Endpoints REST utilisés (relative à `/v1/`)

Sources : `APIEndpoints` dans `src/utils/constants.ts`. Verbes dans `src/methods/`.

**Auth/user** (cf 5.3)

**Nodes** (le gros morceau)
- `user/nodes` — GET (avec query params : `node_details=true`, `node_config=true`, `connectivity_status=true`, `params=true`, `show_tags=true`, `num_records`), DELETE
- `user/nodes/config` — GET (schéma device/service/param)
- `user/nodes/status` — GET (connectivity online/offline + last-seen)
- `user/nodes/params` — GET, PUT (body keyed by device name → `{param: value}`)
- `user/nodes/mapping` — PUT `{user_id?, node_id, secret_key, operation:"add"|"remove"}`, GET (poll)
- `user/nodes/mapping/initiate` — POST challenge-response
- `user/nodes/mapping/verify` — POST finalise

**Sharing**
- `user/nodes/sharing` — GET, PUT, DELETE
- `user/nodes/sharing/requests` — POST, GET, PUT (accept/decline), DELETE

**Groups**
- `user/node_group` — GET, POST (`group_name`, `nodes[]`, `type`, `mutually_exclusive`), PUT, DELETE
- `user/node_group/sharing` — GET, PUT, DELETE
- `user/node_group/sharing/requests` — POST, GET, PUT, DELETE

**OTA**
- `user/nodes/ota_update` — POST push OTA job
- `user/nodes/ota_status` — GET

**Time-series**
- `user/nodes/tsdata` — GET (`param`, `start`, `end`, `aggregate`, `num_records ≤ 200`)
- `user/nodes/simple_tsdata` — GET

**Automations**
- `user/node_automation` — POST, GET, PUT, DELETE (body : `name`, `events[]`, `actions[]`, `event_operator`)
- **Scenes** : pas un endpoint séparé. Modèle node-level service `esp.service.scenes`, set via `user/nodes/params`.
- **Schedules** : idem, service `esp.service.schedules`.

**Push notifications**
- `user/push_notification/mobile_platform_endpoint` — POST `{mobile_device_token, platform:"GCM"|"APNS", endpoint?}`, PUT, DELETE
- Tokens = FCM (Android) / APNS (iOS) bruts (pas de Firebase JS bundle)

**Misc**
- `user/custom_data` — GET, PUT (JSON arbitraire par user : favoris, layout dashboard)
- `mqtt_host` — GET (broker hostname régional)
- `claim/initiate`, `claim/verify` — POST (base URL claim séparée)
- `user/assume_role` — POST AWS STS creds pour KVS WebRTC (skip MVP, `max_node_ids`=5, `max_group_ids`=5)

### 5.5 MQTT (optionnel pour l'appli)

L'appli parle **directement MQTT** quand activé. Fichiers : `src/services/ESPSubscriptionChannels/` (lib `mqtt` 5.x).

```
1. App → GET /v1/mqtt_host
2. App → POST /v1/user/assume_role pour SigV4 creds AWS, puis MQTT-over-WSS
3. Subscribe per-node : node/<id>/params/local (pub), node/<id>/params/remote (sub),
   alerts, events
```

**Options pour le self-host** :
1. Retourner empty/400 sur `mqtt_host` → l'appli fallback sur HTTP polling de `user/nodes/params`. **Plus simple.**
2. Faire pointer `mqtt_host` vers VerneMQ + WSS, avec credentials acceptés (alternative à SigV4 : login MQTT par token JWT échangé contre des creds courtes). **Plus complet.**

### 5.6 Provisioning

- BLE + SoftAP (protocomm/security2 ESP-IDF). Native code dans `src/native-adaptors/implementations/ESPSoftAPAdapter.ts` + modules Kotlin/Swift.
- Après Wi-Fi up, `ESPRMDevice.provision()` :
  - **Legacy `ProvisionType.MQTT`** : envoie secret au device sur `cloud_user_assoc`, puis `PUT /v1/user/nodes/mapping` + poll
  - **Default `ProvisionType.CHAL_RESP`** : `POST /v1/user/nodes/mapping/initiate` → challenge → device → `POST /v1/user/nodes/mapping/verify`

Surface contract = `user/nodes/mapping*`.

### 5.7 Local control (LAN, transparent backend)

- `src/services/ESPTransport/` avec `ESPLocalDiscoveryAdapterInterface` + `ESPLocalControlTransport`
- Discovery mDNS service type `_esp_local_ctrl._tcp.` (constant `ServiceType.ESP_LOCAL_CTRL_TCP`)
- Si `ENABLE_LOCAL_CONTROL=true` et le node advertise, le SDK parle directement au device en protocomm — **bypass complet du backend**
- Le node doit publier un service `esp.service.local_control` dans son `user/nodes/config`. **Rien à faire côté backend.**

### 5.8 Push notifications

- Native adapters (`ESPNotificationAdapter.ts`) → FCM token (Android) / APNS token (iOS) brut, pas de Firebase JS
- Register via `POST /v1/user/push_notification/mobile_platform_endpoint`, body `{mobile_device_token, platform:"GCM"|"APNS"}`
- Backend doit dispatcher (réf Espressif utilise AWS SNS ; self-host = FCM v1 + APNs HTTP/2 directs, phase 9)

### 5.9 Checklist backend minimum pour faire fonctionner l'appli (sans fork)

1. Servir `https://yourhost/v1/*` avec les paths listés en 5.4 (auth + nodes + mapping + custom_data sont must-have ; sharing/groups/OTA/automations/tsdata/push sont feature-flag-gated et dégradent gracieusement)
2. `POST /v1/login2` accepte **les deux bodies** (password ET refresh) ; réponse en **minuscules** `accesstoken`/`idtoken`/`refreshtoken`
3. Issuer JWT que le SDK peut envoyer brut en `Authorization` (sans `Bearer`)
4. (Optionnel) OAuth `/authorize` + `/token` sur authUrl séparé — phase ultérieure
5. `GET /v1/mqtt_host` : retourne VerneMQ (full) ou empty/400 (fallback HTTP polling)
6. Store FCM/APNS tokens via push endpoint
7. Distribuer un **QR de config** carrying `baseUrl`, `version`, `authUrl`, `clientId`

### 5.10 Fichiers de référence (upstream app + SDK)

**App `esp-rainmaker-home`** :
- `.env.example` — constantes build-time (`BASE_URL`, `API_VERSION`, AWS pool IDs, feature flags)
- `config/sdk.config.ts`, `config/agent.config.ts`
- `src/sdk-adaptors/ESPRMBase/index.ts`, `constants.ts`
- `src/native-adaptors/implementations/{ESPSoftAPAdapter,ESPDiscoveryAdapter,ESPOauthAdapter,ESPNotificationAdapter,ESPProvAdapter}.ts`

**SDK `esp-rainmaker-app-sdk-ts`** :
- `src/utils/constants.ts` — `APIEndpoints` (source unique de vérité), `HTTPMethods`, `StorageKeys`, `ServiceType`, `ProvisionType`, `DEFAULT_REST_API_VERSION="v1"`, `DEFAULT_CLAIM_BASE_URL`
- `src/services/ESPRMAPIManager.ts` — fetch wrapper, URL build, header `Authorization: <token>`, 401 → clear tokens
- `src/services/ESPTransport/` — local/cloud transport split, mDNS
- `src/services/ESPSubscriptionChannels/` — MQTT subscription
- `src/methods/ESPRMAuth/{Login,LoginWithOauth,SendSignUpCode,ConfirmSignUp,ForgotPassword,SetNewPassword,RequestLoginOTP,LoginWithOTP,GetLoggedInUser}.ts`
- `src/methods/ESPRMUser/*.ts` (nodes list, custom_data, push registration, assume_role)
- `src/methods/ESPRMNode/*.ts` (config, params, OTA, automations, sharing, tsdata)
- `src/methods/ESPRMGroup/*.ts`, `ESPGroupSharingRequest/*.ts`, `ESPNodeSharingRequest/*.ts`

---

## 6. Inventaire Swagger (résumé)

4 fichiers dans `swagger/` :

| Fichier | Lignes | Auth | Endpoints |
|---|---|---|---|
| `Rainmaker_Swagger.yaml` | 22 405 | JWT user / admin | ~80 (user + admin) |
| `Rainmaker_Node_Swagger.yml` | 875 | mTLS node | 7 (config, params, otafetch, otastatus, file, cmd_resp) |
| `Rainmaker_SuperAdmin_Swagger.yml` | 11 718 | JWT super admin | ~46 (cognito, deployment, push platform, matter…) |
| `Rainmaker_Claiming_External_swagger.yml` | 205 | JWT optionnel | 2 (initiate, verify) |

**Schémas data réutilisés (à modéliser côté backend)** :
- `NodeInfo` : node_id, name, fw_version, type, model, node_status, registration_timestamp, metadata
- `NodeGroup` : group_id, group_name, dynamic/static membership, type, mutually_exclusive, parent_id
- `NodeConfiguration` : node_id, config_version, devices[], services[]
- `DeviceParameters` : device name, type, properties array (cf §4.7)
- `OTAImage` : ota_image_id, image_name, fw_version, file_size, S3 URL
- `OTAJob` : job_id, status, nodes array, scheduled/rollout mode
- `UserNodeSharing` : node_id, users map, sharing_permissions
- `EventFilter` : event_type, entity_id, integration enablement
- `AutomationTrigger` : automation_id, cron/daylight rules, action metadata
- `TimeSeriesData` : node_id, timestamps, data points, aggregations

**Réponses d'erreur** suivent le format `{"status": "failure", "description": "...", "error_code": <int>}` avec codes documentés en clair dans les descriptions (ex: `100009` = "Node Id is missing"). Voir `app/core/errors.py` pour les codes principaux.

---

## 7. Modèle de données

Tables SQLAlchemy sous `app/models/`, créées par les 5 migrations Alembic
`alembic/versions/0001..0005_*.py`.

### Présentes (phases 0-8)

**Auth & user (`app/models/user.py`, migration `0001`)**
- `users` — id (uuid), user_name (uniq), email, password_hash, phone_number,
  full_name, status (`unconfirmed|confirmed|disabled`), is_super_admin, is_admin,
  mfa_enabled, custom_data (JSON), confirm_code/_exp, reset_code/_exp,
  login_otp/_exp, last_login_at, created_at, updated_at
- `refresh_tokens` — id, user_id (FK), jti (uniq), expires_at — révocation par jti

**Devices (`app/models/node.py` + `device_provisioning.py`, migrations `0001`+`0002`)**
- `nodes` — node_id (PK), registration_ts, online, last_seen_at, tags (JSON), metadata
- `node_attributes` — (node_id, name) → value
- `node_configs` — schéma devices/services/params publié par le node
- `node_params_shadow` — état courant (miroir `params/local`)
- `node_certificates` — serial (PK), cn, pem, revoked
- `device_provisioning` — pre-claim (HMAC self-claim ou JWT assisted)
- `claim_challenges` — flux challenge-response self-claim

**User ↔ Node mapping (`app/models/user_node.py`, migration `0001`)**
- `user_node_mappings` — (user_id, node_id), role (primary|secondary), `primary` bool
- `mapping_challenges` — flux secret_key (MQTT) + chal-resp (HTTP)

**Sharing & groups (`app/models/sharing.py`, migration `0004`)**
- `node_sharing` — (node_id, user_id) avec permissions
- `node_sharing_requests` — pending/accepted/declined
- `node_groups` — id, user_id, name, parent_id (arborescent), type, mutually_exclusive
- `node_group_nodes` — N:M (group_id, node_id)

**OTA (`app/models/ota.py`, migration `0003`)**
- `ota_images` — image_id, version, file_size, md5, s3_key
- `ota_jobs` — job_id, image_id, status, scheduling
- `ota_job_nodes` — N:M (job_id, node_id) + per-node status

**Automations & time-series (`app/models/automation.py`, migration `0005`)**
- `automations` — id, user_id, name, enabled, event_operator, events (JSON),
  actions (JSON), metadata
- `tsdata` — **hypertable Timescale** (ts, node_id, device_name, param_name,
  data_type, value_int/value_float/value_text/value_json) avec
  `create_hypertable('tsdata', 'ts', chunk_time_interval=>'1 day')`

### Hors-scope MVP (non créées)

- `procrastinate_*` — la lib crée ses tables au premier `procrastinate schema --apply` ;
  le worker stub actuel ne les touche pas
- `rate_limit_buckets`, `revoked_tokens_cache` — applicatif au-dessus du JWT TTL pour
  l'instant ; à matérialiser si on ajoute du throttling agressif
- `push_endpoints` — endpoints FCM/APNs : déposés Phase 9

---

## 8. Phases d'implémentation TDD

Méthodologie : **red → green → refactor** piloté par le skill `tdd`. Chaque feature
démarre par un test d'intégration qui reproduit un cas réel (extrait du SDK TS ou du
firmware ESP).

### Fixtures partagées (`tests/conftest.py`) à construire

- `fake_node` — client `aiomqtt` avec cert signé par CA de test, expose `publish(topic, payload)`, `subscribe(pattern, callback)`, `await_message(topic_pattern, timeout=)`. Reproduit le firmware.
- `fake_app` — `httpx.AsyncClient` pré-authentifié (login par fixture), reproduit les appels exacts du SDK TS (`Authorization: <token>` sans `Bearer`, payloads JSON minuscules).
- `await_eventually(condition, timeout=)` — pour flux MQTT asynchrones
- `db` — AsyncSession transactionnelle rollback après test
- `broker` — VerneMQ via testcontainers ou broker Python `amqtt` in-process
- `garage` — Garage ou MinIO via testcontainers (S3 API identique)

### Scénarios E2E par phase — toutes implémentées ✅

| Phase | Scénarios clés | Statut | Tests |
|---|---|---|---|
| 1 — Auth | signup→confirm→login→refresh complet ; JWT brut accepté, Bearer rejeté ; logout révoque refresh | ✅ | `tests/integration/test_auth.py` |
| 2 — Claiming | self-claim HMAC → cert PEM avec CN=node_id ; cert signé par notre CA intermédiaire ; cert autorise MQTT seulement sur `node/<cn>/#` ; cert révoqué → connexion MQTT refusée | ✅ | `tests/integration/test_claiming.py` + `live_verify.sh` T2.5 |
| 3 — Node API mTLS | `PUT /v1/node/config` persiste ; otafetch retourne job ; rejet si pas de cert client | ✅ | `tests/integration/test_node_api.py` + `live_verify_prod_like.sh` E.1.4 |
| 4 — mqtt-ingestor | topic config persiste ; topic user/mapping match challenge ; params/local update shadow ; on_client_online/offline mettent à jour `nodes.online` | ✅ | `tests/integration/test_mqtt_ingestor.py` |
| 5 — App nodes | filtres `node_details`/`config`/`params`/`connectivity` ; PUT params publie sur `params/remote` ; echo `params/local` mis à jour ; user ne voit pas les nodes d'un autre | ✅ | `tests/integration/test_user_nodes.py` + `live_verify.sh` T2.4 |
| 6 — OTA | upload signed URL Garage ; job → `otaurl` publié ; status agrégé ; user peut trigger OTA pour son node | ✅ | `tests/integration/test_ota.py` (round-trip Garage validé en commit `ae7c4a0`) |
| 7 — Sharing/groups | share grants secondary access ; revoke bloque immédiatement ; group hiérarchie + mutually_exclusive ; group sharing propage aux members | ✅ | `tests/integration/test_sharing.py` + `live_verify.sh` T7.x |
| 8 — Automations/ts | param-change trigger → action exécutée ; cron trigger fire ; tsdata avec agrégation `time_bucket` Timescale | ⚠️ partiel | `tests/integration/test_automations_tsdata.py` + `live_verify.sh` T9.x — CRUD OK, runtime evaluator absent (worker stub) |

**Score global** : 78 tests (9 unit + 69 integration), 21 live-verify cases (Tests
#2/#7/#9), 13 prod-like cases (NGINX mTLS + scale + read-only fs) — 112 cases verts.

### Gap restant Phase 8

Le **runtime evaluator** d'automations est identifié comme manquant (cf. note
T9.5 dans `live_verify.sh`). La CRUD surface fonctionne (POST/GET/PUT/DELETE),
mais aucun worker ne consomme les changements de shadow pour déclencher les
actions. Pour le faire : task procrastinate qui écoute `params/local` (LISTEN/NOTIFY
ou 2e consumer MQTT), évalue le JSONB events, publie sur `params/remote`. Hors
MVP.

### Validation manuelle complémentaire

- `scripts/fake_node.py` — simulateur paho-mqtt qui fait un cycle complet
  (provision-key, self-claim, assisted-claim, otafetch mTLS) ; utilisé par les
  2 scripts `live_verify*.sh`
- `scripts/live_verify.sh` — 21 cases couvrant Tests #2/#7/#9 contre la stack compose
- `scripts/live_verify_prod_like.sh` — 13 cases NGINX-fronted (mTLS Ingress
  emulation, multi-replica scale, read-only fs)
- Appli RN réelle avec QR config pointant vers le déploiement dev — à faire
- ESP32 physique flashé `esp-rainmaker` — à faire (Palier D du `docs/TEST_PLAN.md`)

---

## 9. État actuel du code — MVP atteint (phases 0-8)

### Application Python (`app/`, 71 fichiers .py)

```
app/
├── __init__.py                # __version__
├── main.py                    # FastAPI factory + lifespan + exception handler
├── core/
│   ├── config.py              # pydantic-settings (env RM_*)
│   ├── errors.py              # RainmakerError + ErrorCode (format SDK TS)
│   ├── logging.py             # structlog JSON
│   └── security.py            # JWT HS256 + bcrypt + secret generation
├── db/
│   ├── base.py                # DeclarativeBase + naming convention + TimestampMixin
│   └── session.py             # AsyncSessionLocal + get_db()
├── models/                    # SQLAlchemy 2 typed (cf §7)
│   ├── user.py, node.py, user_node.py, device_provisioning.py
│   ├── sharing.py, ota.py, automation.py
├── schemas/                   # pydantic v2
│   ├── auth.py, user.py, common.py
├── pki/
│   └── ca.py                  # CA chain + CSR signing (Phase 2)
├── mqtt/
│   ├── publisher.py           # backend → device publish (params/remote, otaurl)
│   ├── router.py              # ingestor topic dispatch
│   └── topics.py              # topic constants
├── services/                  # business logic
│   ├── auth.py, access.py     # authn + RBAC
│   ├── claim.py, mapping.py   # provisioning & user↔node mapping
│   ├── node.py                # nodes CRUD + shadow merge
│   ├── sharing.py, groups.py  # Phase 7
│   ├── ota.py, storage.py     # Phase 6 + Garage S3
│   ├── automations.py, tsdata.py  # Phase 8
│   ├── vmq_authz.py           # 4 webhooks VerneMQ
│   └── email.py               # smtp4dev / real SMTP
├── api/
│   ├── internal/vmq_authz.py  # /auth/on_register|publish|subscribe + on_client_offline
│   └── v1/
│       ├── router.py
│       ├── deps/
│       │   ├── auth.py        # JWT brut (rejette Bearer)
│       │   ├── admin.py       # is_admin / is_super_admin
│       │   ├── mtls.py        # X-SSL-Client-CN extraction
│       │   └── db.py          # async session
│       └── routes/
│           ├── health.py, meta.py, auth.py, user.py
│           ├── claim.py, node.py (mTLS), user_nodes.py
│           ├── sharing.py, groups.py
│           ├── ota_admin.py, ota_user.py
│           ├── automations.py, tsdata.py
└── entrypoints/               # 4 commands, image Docker unique
    ├── api.py                 # uvicorn → app.main:app
    ├── vmq_authz.py           # FastAPI minimal port 8001
    ├── mqtt_ingestor.py       # aiomqtt shared-subscription
    └── worker.py              # procrastinate stub (runtime automations TBD)
```

### Migrations (`alembic/versions/`)

- `0001_init.py` — users, refresh_tokens, nodes, node_attributes, node_configs,
  node_params_shadow, node_certificates, user_node_mappings, mapping_challenges
- `0002_claiming.py` — device_provisioning, claim_challenges
- `0003_ota.py` — ota_images, ota_jobs, ota_job_nodes
- `0004_sharing_groups.py` — node_sharing, node_sharing_requests, node_groups,
  node_group_nodes
- `0005_automations_tsdata.py` — automations + tsdata **hypertable Timescale**

### Tests (`tests/`)

- `tests/unit/` — 9 cases : smoke (healthz, apiversions, mqtt_host), security
  (hash/verify password, JWT mint/decode round-trip + tampered/expired)
- `tests/integration/` — 69 cases via testcontainers Postgres+TimescaleDB :
  test_auth, test_claiming, test_node_api, test_mqtt_ingestor, test_user_nodes,
  test_ota, test_sharing, test_automations_tsdata
- `tests/conftest.py` — fixtures `client`, `client_with_db`, `db`, `migrated_db`,
  `pg_dsn` (testcontainers session-scoped)

### Scripts (`scripts/`)

- `gen_pki.py` — root + intermediate + server certs pour dev
- `init_garage.sh` — bootstrap one-shot layout + key + bucket Garage
- `fake_node.py` — simulateur firmware paho-mqtt (`provision-key`, `self-claim`,
  `assisted-claim`, `otafetch`)
- `live_verify.sh` — 21 cases (Tests #2/#7/#9 contre compose dev)
- `live_verify_prod_like.sh` — 13 cases (NGINX mTLS + scale + read-only fs)

### Déploiement

- `docker-compose.yml` — dev stack (postgres+timescale, garage, vernemq, smtp4dev,
  nginx, api, vmq-authz, mqtt-ingestor, worker) avec images via miroir
  `ghcr.io/bbinet/esp-rainmaker-server/*`
- `deploy/compose/docker-compose.prod.yml` — overlay prod (restart policies,
  resource limits, log rotation, postgres-backup + garage-backup)
- `deploy/nginx/rainmaker.conf` — 3 vhosts (api.local 443, claim.local 444,
  node.local 445 avec mTLS verify)
- `deploy/docker/Dockerfile` — multi-stage, base `python:3.11-slim-trixie`,
  uid 1000 non-root, 4 entrypoints (CMD override par service)
- `deploy/garage/garage.toml`, `deploy/vernemq/vernemq.conf`
- `deploy/k8s/` — Kustomize base (api, vmq-authz, mqtt-ingestor, worker,
  postgres StatefulSet, garage StatefulSet, vernemq StatefulSet, ingress 3
  vhosts, NetworkPolicies, migrations Job, HPA) + overlays `dev/staging/prod`

### CI/CD (`.github/workflows/`)

- `ci.yml` — `lint-type-unit` (container Trixie : ruff + mypy strict + 9 unit
  tests) + `integration` (host : 69 testcontainers tests) + `docker-build`
  (image multi-stage) ; `live-verify` job en cours d'ajout (PR #3)
- `publish.yml` — sur tag `v*.*.*` : build multi-arch (amd64 + arm64), push
  `ghcr.io/bbinet/esp-rainmaker-server:<tag>` + SBOM + provenance
- `mirror.yml` — cron weekly + dispatch : retag 7 images Docker Hub vers
  `ghcr.io/bbinet/esp-rainmaker-server/*` pour éviter le rate-limit DH

### Documentation

- `README.md` — entrée projet, quickstart, deployment targets table
- `docs/REFERENCE.md` — ce document
- `docs/TEST_PLAN.md` — 6 paliers A→F (A+B validés)
- `deploy/compose/README.md` — déploiement single-host
- `deploy/k8s/README.md` — déploiement multi-host

### Hors MVP (à faire après)

- Phase 9 push notifications (FCM v1 + APNs HTTP/2)
- Runtime evaluator des automations (cf §8 gap T9.5)
- OAuth tiers (`/authorize` + `/token`)
- Admin / super-admin endpoints
- Matter, video streaming, `assume_role` AWS STS
- Console admin web

---

## 10. Conventions de code

- Python 3.11+, type hints partout
- `from __future__ import annotations` en tête de chaque fichier
- Async-first (FastAPI, SQLAlchemy 2 async, asyncpg)
- `ruff` (line-length 100) + `mypy --strict`
- Tests pytest-asyncio mode `auto`
- Schémas pydantic v2, modèles SQLAlchemy 2 typed
- Logs structurés (structlog JSON)
- Pas de commentaires explicatifs sauf invariant subtil ou workaround

## 11. Points d'attention spécifiques RainMaker

⚠️ **JWT sans `Bearer`** — le SDK TS envoie `Authorization: <token>` brut. Toute dépendance auth doit accepter ce format et **rejeter** le préfixe `Bearer ` (selon contrat strict).

⚠️ **Clés de réponse en minuscules** sur `/login2` : `accesstoken` (pas `access_token`), `idtoken`, `refreshtoken`.

⚠️ **Erreur format** : `{"status": "failure", "description": "...", "error_code": <int>}`. Le SDK inspecte `status` et `description` ; certains flows aussi `error_code`.

⚠️ **Topic MQTT user/mapping** : le device publie avec `node_id` ET `user_id` ET `secret_key` ; le backend match sur `secret_key` (le `user_id` côté device vient du téléphone via protocomm, le `node_id` est son propre identifiant).

⚠️ **Cert mTLS** : le CN du cert device = `node_id`. C'est l'invariant central. Tout extracteur d'identité device (mqtt-ingestor, vmq-authz, api-node) part de là.

⚠️ **QoS 1 partout** : nécessaire car la state machine user-mapping côté firmware attend les PUBACK pour avancer.

⚠️ **Le backend ne stocke pas les payloads MQTT bruts** — il les parse et upsert dans des tables typées. Pas de log MQTT en BDD.
