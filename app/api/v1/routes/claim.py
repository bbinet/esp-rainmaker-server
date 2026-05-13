"""Claiming endpoints — served at the root, not under /v1.

The mobile SDK targets ``DEFAULT_CLAIM_BASE_URL`` (typically a separate
hostname like ``claim.<domain>``) for these routes; we deliver them
from the same backend Service, with Ingress routing on hostname.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.api.v1.deps.db import get_db
from app.core.errors import invalid_request
from app.models.user import User
from app.services import claim as claim_service

router = APIRouter(tags=["claim"])


@router.post("/claim/initiate")
async def claim_initiate(
    payload: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    mac_addr = payload.get("mac_addr")
    platform = payload.get("platform")
    if not mac_addr or not platform:
        raise invalid_request("mac_addr and platform required")

    if authorization:
        user = await _resolve_user_from_header(authorization, db)
        return await claim_service.initiate_assisted_claim(
            db, user_id=user.id, mac_addr=mac_addr, platform=platform
        )
    return await claim_service.initiate_self_claim(db, mac_addr=mac_addr, platform=platform)


@router.post("/claim/verify")
async def claim_verify(
    payload: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    csr = payload.get("csr")
    if not csr:
        raise invalid_request("csr required")
    csr_pem = csr.encode()

    if authorization:
        user = await _resolve_user_from_header(authorization, db)
        return await claim_service.verify_assisted_claim(db, user_id=user.id, csr_pem=csr_pem)

    auth_id = payload.get("auth_id")
    challenge_response = payload.get("challenge_response")
    if not auth_id or not challenge_response:
        raise invalid_request("auth_id and challenge_response required for self-claim")
    return await claim_service.verify_self_claim(
        db,
        auth_id=auth_id,
        challenge_response=challenge_response,
        csr_pem=csr_pem,
    )


async def _resolve_user_from_header(authorization: str, db: AsyncSession) -> User:
    """Reuse the same JWT extraction as get_current_user but from a header value."""
    import jwt

    from app.core.errors import unauthorized
    from app.core.security import decode_token

    token = authorization.strip()
    if token.lower().startswith("bearer "):
        raise unauthorized("Bearer-prefixed tokens are not supported")
    try:
        payload = decode_token(token, expected_kind="access")
    except jwt.PyJWTError as exc:
        raise unauthorized("Invalid token") from exc
    try:
        user_id = uuid.UUID(payload["sub"])
    except Exception as exc:  # noqa: BLE001
        raise unauthorized("Invalid token") from exc

    from sqlalchemy import select

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.status != "confirmed":
        raise unauthorized("User not found")
    _ = get_current_user  # signal intent
    return user
