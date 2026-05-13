"""Integration tests for Phase 2 — device claiming & PKI.

Reproduces the firmware-side flows from
``esp-rainmaker/components/esp_rainmaker/src/core/esp_rmaker_claim.c``:

* **Self-claim (HMAC)**: device with eFuse key calls /claim/initiate without
  auth, receives ``auth_id`` + ``challenge``, replies with the HMAC-SHA256
  of the challenge over the eFuse key plus a CSR.
* **Assisted claim**: device proxied by the phone; /claim/initiate carries a
  user JWT and yields ``node_id``; /claim/verify exchanges CSR → cert.

Cert returned must have CN equal to the issued ``node_id`` and be signed by
the configured intermediate CA. The vmq-authz hooks then authorise MQTT
only on the matching ``node/<cn>/#`` namespace.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

pytestmark = pytest.mark.asyncio


def _generate_csr(common_name: str | None = None) -> tuple[bytes, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name or "placeholder")]
    )
    csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.PEM), key


async def _provision_test_hmac_key(db, *, mac_addr: str, key: bytes) -> None:
    """Stage a manufacturer-provisioned HMAC key for self-claim tests."""
    from app.models.device_provisioning import DeviceProvisioning

    db.add(
        DeviceProvisioning(
            mac_addr=mac_addr,
            platform="ESP32S3",
            hmac_key=key,
        )
    )
    await db.commit()


# ---------------- Self-claim (HMAC) ----------------


async def test_self_claim_initiate_returns_auth_id_and_challenge(
    client_with_db: httpx.AsyncClient, db
) -> None:
    mac_addr = "7CDFA1000001"
    await _provision_test_hmac_key(db, mac_addr=mac_addr, key=b"k" * 32)

    response = await client_with_db.post(
        "/claim/initiate",
        json={"mac_addr": mac_addr, "platform": "ESP32S3"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("auth_id")
    assert body.get("challenge")


async def test_self_claim_verify_returns_signed_certificate(
    client_with_db: httpx.AsyncClient, db
) -> None:
    mac_addr = "7CDFA1000002"
    hmac_key = b"k" * 32
    await _provision_test_hmac_key(db, mac_addr=mac_addr, key=hmac_key)

    init = (
        await client_with_db.post(
            "/claim/initiate",
            json={"mac_addr": mac_addr, "platform": "ESP32S3"},
        )
    ).json()

    challenge_bytes = base64.b64decode(init["challenge"])
    challenge_response = base64.b64encode(
        hmac.new(hmac_key, challenge_bytes, hashlib.sha256).digest()
    ).decode()

    csr_pem, _ = _generate_csr()

    verify = await client_with_db.post(
        "/claim/verify",
        json={
            "auth_id": init["auth_id"],
            "challenge_response": challenge_response,
            "csr": csr_pem.decode(),
            "send_mqtt_host": True,
        },
    )
    assert verify.status_code == 200, verify.text
    body = verify.json()
    cert_pem = body["certificate"].encode()
    cert = x509.load_pem_x509_certificate(cert_pem)
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn  # CN must be the issued node_id
    assert body.get("mqtt_host")

    # Verify cert chains to our intermediate CA.
    from app.pki.ca import load_intermediate_ca

    intermediate_cert, _ = load_intermediate_ca()
    intermediate_cert.public_key().verify(
        cert.signature,
        cert.tbs_certificate_bytes,
        ec.ECDSA(cert.signature_hash_algorithm),
    )


async def test_self_claim_verify_rejects_wrong_challenge_response(
    client_with_db: httpx.AsyncClient, db
) -> None:
    mac_addr = "7CDFA1000003"
    await _provision_test_hmac_key(db, mac_addr=mac_addr, key=b"k" * 32)

    init = (
        await client_with_db.post(
            "/claim/initiate",
            json={"mac_addr": mac_addr, "platform": "ESP32S3"},
        )
    ).json()
    csr_pem, _ = _generate_csr()

    bad = await client_with_db.post(
        "/claim/verify",
        json={
            "auth_id": init["auth_id"],
            "challenge_response": base64.b64encode(b"\x00" * 32).decode(),
            "csr": csr_pem.decode(),
        },
    )
    assert bad.status_code == 400


# ---------------- Assisted claim ----------------


async def _signup_login(client: httpx.AsyncClient, db) -> str:
    body = {"user_name": "claimer@example.com", "password": "Claimer-Pass-1!"}
    await client.post("/v1/user2", json=body)

    from sqlalchemy import select

    from app.models.user import User

    user = (await db.execute(select(User).where(User.user_name == body["user_name"]))).scalar_one()
    await client.put(
        "/v1/user2", json={"user_name": body["user_name"], "verification_code": user.confirm_code}
    )
    login = (await client.post("/v1/login2", json=body)).json()
    return login["accesstoken"]


async def test_assisted_claim_initiate_returns_node_id(
    client_with_db: httpx.AsyncClient, db
) -> None:
    token = await _signup_login(client_with_db, db)
    response = await client_with_db.post(
        "/claim/initiate",
        headers={"Authorization": token},
        json={"mac_addr": "AABBCCDDEE01", "platform": "ESP32"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("node_id")


async def test_assisted_claim_verify_signs_cert(client_with_db: httpx.AsyncClient, db) -> None:
    token = await _signup_login(client_with_db, db)
    init = (
        await client_with_db.post(
            "/claim/initiate",
            headers={"Authorization": token},
            json={"mac_addr": "AABBCCDDEE02", "platform": "ESP32"},
        )
    ).json()

    csr_pem, _ = _generate_csr()
    verify = await client_with_db.post(
        "/claim/verify",
        headers={"Authorization": token},
        json={"csr": csr_pem.decode(), "send_mqtt_host": True},
    )
    assert verify.status_code == 200, verify.text
    body = verify.json()
    cert_pem = body["certificate"].encode()
    cert = x509.load_pem_x509_certificate(cert_pem)
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == init["node_id"]


# ---------------- vmq-authz webhook ----------------


async def test_vmq_authz_allows_publish_on_own_namespace(client_with_db, db) -> None:
    """When VerneMQ POSTs /auth/on_publish with username=<node_id>, the
    service authorises any topic starting with `node/<node_id>/`."""
    # Register a node certificate manually for the test.
    from datetime import UTC, datetime, timedelta

    from app.models.node import NodeCertificate

    node_id = "test-node-authz-1"
    db.add(
        NodeCertificate(
            serial="ser-1",
            node_id=node_id,
            cert_pem="",
            cn=node_id,
            not_before=datetime.now(UTC),
            not_after=datetime.now(UTC) + timedelta(days=365),
        )
    )
    await db.commit()

    response = await client_with_db.post(
        "/auth/on_publish",
        json={
            "username": node_id,
            "client_id": "device",
            "mountpoint": "",
            "qos": 1,
            "topic": f"node/{node_id}/params/local",
            "retain": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "ok"


async def test_vmq_authz_rejects_publish_on_other_namespace(client_with_db, db) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models.node import NodeCertificate

    db.add(
        NodeCertificate(
            serial="ser-2",
            node_id="node-A",
            cert_pem="",
            cn="node-A",
            not_before=datetime.now(UTC),
            not_after=datetime.now(UTC) + timedelta(days=365),
        )
    )
    await db.commit()

    response = await client_with_db.post(
        "/auth/on_publish",
        json={
            "username": "node-A",
            "client_id": "device",
            "topic": "node/other-node/params/local",
            "qos": 1,
            "retain": False,
        },
    )
    body = response.json()
    assert body["result"] != "ok"


async def test_vmq_authz_rejects_revoked_cert(client_with_db, db) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models.node import NodeCertificate

    db.add(
        NodeCertificate(
            serial="ser-3",
            node_id="node-R",
            cert_pem="",
            cn="node-R",
            not_before=datetime.now(UTC),
            not_after=datetime.now(UTC) + timedelta(days=365),
            revoked=True,
        )
    )
    await db.commit()

    response = await client_with_db.post(
        "/auth/on_register",
        json={
            "peer_addr": "1.2.3.4",
            "peer_port": 12345,
            "username": "node-R",
            "client_id": "device",
            "mountpoint": "",
            "clean_session": True,
            "password": "ignored",
        },
    )
    body = response.json()
    assert body["result"] != "ok"
