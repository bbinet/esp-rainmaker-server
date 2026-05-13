"""User ↔ node mapping flows.

Two paths from the SDK:

* **Legacy secret_key** (``PUT /v1/user/nodes/mapping``)
    Phone generates a random secret_key, hands it to the device through
    protocomm, and records the (node_id, secret_key) pair here. The
    device echoes the secret_key on ``node/<id>/user/mapping``; the
    ingestor (Phase 4) matches the row and creates the binding.

* **Challenge-response** (``POST /v1/user/nodes/mapping/initiate``
    + ``/verify``)
    Backend issues a per-(user, node) challenge that the phone proxies
    to the device; verify accepts the device's reply and finalises the
    mapping.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RainmakerError, invalid_request
from app.models.user_node import MappingChallenge, UserNodeMapping


def _now() -> datetime:
    return datetime.now(UTC)


async def request_legacy_mapping(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    node_id: str,
    secret_key: str,
    operation: str = "add",
) -> str:
    request_id = secrets.token_urlsafe(16)
    db.add(
        MappingChallenge(
            request_id=request_id,
            user_id=user_id,
            node_id=node_id,
            secret_key=secret_key,
            status="pending",
            operation=operation,
            expires_at=_now() + timedelta(hours=1),
        )
    )
    await db.commit()
    return request_id


async def get_mapping_status(
    db: AsyncSession, *, user_id: uuid.UUID, request_id: str
) -> dict[str, str]:
    row = (
        await db.execute(
            select(MappingChallenge).where(
                MappingChallenge.request_id == request_id,
                MappingChallenge.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise RainmakerError(404, 100015, "Mapping request not found")
    return {"request_id": request_id, "request_status": row.status}


async def initiate_challenge(
    db: AsyncSession, *, user_id: uuid.UUID, node_id: str
) -> dict[str, str]:
    request_id = secrets.token_urlsafe(16)
    challenge = secrets.token_urlsafe(32)
    db.add(
        MappingChallenge(
            request_id=request_id,
            user_id=user_id,
            node_id=node_id,
            challenge=challenge,
            status="pending",
            operation="add",
            expires_at=_now() + timedelta(minutes=10),
        )
    )
    await db.commit()
    return {"request_id": request_id, "challenge": challenge}


async def verify_challenge(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    request_id: str,
    challenge_response: str,
) -> dict[str, str]:
    row = (
        await db.execute(
            select(MappingChallenge).where(
                MappingChallenge.request_id == request_id,
                MappingChallenge.user_id == user_id,
                MappingChallenge.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise invalid_request("Mapping request not found or already used")
    if row.expires_at < _now():
        raise invalid_request("Mapping request expired")
    if row.challenge is None:
        raise invalid_request("Mapping not in challenge-response mode")
    if not secrets.compare_digest(row.challenge, challenge_response):
        raise invalid_request("Invalid challenge_response")

    existing = (
        await db.execute(
            select(UserNodeMapping).where(
                UserNodeMapping.user_id == user_id,
                UserNodeMapping.node_id == row.node_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            UserNodeMapping(
                user_id=user_id,
                node_id=row.node_id,
                role="primary",
                primary=True,
            )
        )
    row.status = "completed"
    row.completed_at = _now()
    await db.commit()
    return {"request_id": request_id, "request_status": "completed"}
