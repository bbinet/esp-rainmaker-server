"""Device claiming service.

Implements the two flows from the firmware
(``esp_rmaker_claim.c`` in ``espressif/esp-rainmaker``):

* **Self-claim (HMAC)**: ``POST /claim/initiate`` without auth issues a
  challenge tied to a (mac, platform). The device replies with the
  HMAC-SHA256 of the challenge using its eFuse key. ``POST /claim/verify``
  verifies the proof and signs the CSR. The eFuse key has been uploaded
  to ``device_provisioning`` by the manufacturer ahead of time.
* **Assisted**: ``POST /claim/initiate`` carries a user JWT; we assign a
  ``node_id`` immediately. ``POST /claim/verify`` (same auth) just signs
  the CSR after re-checking the ``mac → node_id`` binding.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ErrorCode, RainmakerError, invalid_request
from app.models.device_provisioning import ClaimChallenge, DeviceProvisioning
from app.models.node import Node, NodeCertificate
from app.pki.ca import sign_device_csr


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_auth_id() -> str:
    return secrets.token_urlsafe(16)


def _generate_node_id(mac_addr: str) -> str:
    # node_id = lowercased MAC for stability; firmware uses this form too.
    return mac_addr.lower()


# ---------------- Self-claim ----------------


async def initiate_self_claim(db: AsyncSession, *, mac_addr: str, platform: str) -> dict[str, str]:
    prov = (
        await db.execute(select(DeviceProvisioning).where(DeviceProvisioning.mac_addr == mac_addr))
    ).scalar_one_or_none()
    if prov is None:
        raise RainmakerError(400, ErrorCode.INVALID_REQUEST, "Device not registered for self-claim")

    challenge = secrets.token_bytes(32)
    auth_id = _generate_auth_id()
    node_id = _generate_node_id(mac_addr)
    settings = get_settings()
    db.add(
        ClaimChallenge(
            auth_id=auth_id,
            mac_addr=mac_addr,
            platform=platform,
            node_id=node_id,
            challenge=challenge,
            mode="self",
            expires_at=_now() + timedelta(seconds=settings.claim_challenge_ttl_seconds),
        )
    )
    await db.commit()
    return {
        "auth_id": auth_id,
        "challenge": base64.b64encode(challenge).decode(),
    }


async def verify_self_claim(
    db: AsyncSession,
    *,
    auth_id: str,
    challenge_response: str,
    csr_pem: bytes,
) -> dict[str, str]:
    challenge_row = (
        await db.execute(select(ClaimChallenge).where(ClaimChallenge.auth_id == auth_id))
    ).scalar_one_or_none()
    if challenge_row is None or challenge_row.mode != "self":
        raise invalid_request("Unknown auth_id")
    if challenge_row.status != "pending":
        raise invalid_request("Challenge already used")
    if challenge_row.expires_at < _now():
        raise invalid_request("Challenge expired")

    prov = (
        await db.execute(
            select(DeviceProvisioning).where(DeviceProvisioning.mac_addr == challenge_row.mac_addr)
        )
    ).scalar_one_or_none()
    if prov is None:
        raise invalid_request("Device provisioning missing")

    try:
        expected = hmac.new(prov.hmac_key, challenge_row.challenge, hashlib.sha256).digest()
        got = base64.b64decode(challenge_response)
    except Exception as exc:  # noqa: BLE001
        raise invalid_request("Invalid challenge_response encoding") from exc

    if not hmac.compare_digest(expected, got):
        challenge_row.attempts += 1
        await db.commit()
        raise invalid_request("Invalid challenge_response")

    challenge_row.status = "verified"
    challenge_row.verified_at = _now()
    await db.commit()
    return await _issue_certificate(db, node_id=challenge_row.node_id, csr_pem=csr_pem)


# ---------------- Assisted ----------------


async def initiate_assisted_claim(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    mac_addr: str,
    platform: str,
) -> dict[str, str]:
    node_id = _generate_node_id(mac_addr)
    settings = get_settings()
    db.add(
        ClaimChallenge(
            mac_addr=mac_addr,
            platform=platform,
            node_id=node_id,
            user_id=user_id,
            mode="assisted",
            expires_at=_now() + timedelta(seconds=settings.claim_challenge_ttl_seconds),
        )
    )
    await db.commit()
    return {"node_id": node_id}


async def verify_assisted_claim(
    db: AsyncSession, *, user_id: uuid.UUID, csr_pem: bytes
) -> dict[str, str]:
    row = (
        await db.execute(
            select(ClaimChallenge)
            .where(
                ClaimChallenge.user_id == user_id,
                ClaimChallenge.mode == "assisted",
                ClaimChallenge.status == "pending",
            )
            .order_by(ClaimChallenge.created_at.desc())
        )
    ).scalar_one_or_none()
    if row is None:
        raise invalid_request("No pending claim for this user")
    if row.expires_at < _now():
        raise invalid_request("Claim expired")

    row.status = "verified"
    row.verified_at = _now()
    await db.commit()
    return await _issue_certificate(db, node_id=row.node_id, csr_pem=csr_pem)


# ---------------- Shared ----------------


async def _issue_certificate(db: AsyncSession, *, node_id: str, csr_pem: bytes) -> dict[str, str]:
    try:
        chain_pem, serial, not_before, not_after = sign_device_csr(csr_pem, node_id=node_id)
    except Exception as exc:  # noqa: BLE001
        raise invalid_request(f"CSR signing failed: {exc}") from exc

    node = (await db.execute(select(Node).where(Node.node_id == node_id))).scalar_one_or_none()
    if node is None:
        db.add(Node(node_id=node_id, registration_ts=_now()))

    db.add(
        NodeCertificate(
            serial=serial,
            node_id=node_id,
            cn=node_id,
            cert_pem=chain_pem.decode(),
            not_before=not_before,
            not_after=not_after,
        )
    )
    await db.commit()

    settings = get_settings()
    return {
        "certificate": chain_pem.decode(),
        "mqtt_host": f"{settings.mqtt_broker_host}:{settings.mqtt_broker_port}",
        "mqtt_cred_host": f"{settings.mqtt_broker_host}:{settings.mqtt_broker_port}",
    }
