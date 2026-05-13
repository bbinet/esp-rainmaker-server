"""JWT authentication dependency.

The mobile app sends the raw access token in the ``Authorization`` header
without the ``Bearer`` prefix. We mirror that contract: ``Bearer ...`` is
explicitly rejected, since that would mean a client is not using the
official SDK and we'd rather fail loudly than silently accept divergent
behaviours.
"""

from __future__ import annotations

import uuid

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.db import get_db
from app.core.errors import unauthorized
from app.core.security import decode_token
from app.models.user import User


def _extract_token(request: Request) -> str:
    header = request.headers.get("Authorization")
    if not header:
        raise unauthorized("Authorization header missing")
    if header.lower().startswith("bearer "):
        raise unauthorized("Bearer-prefixed tokens are not supported")
    return header.strip()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_token(request)
    try:
        payload = decode_token(token, expected_kind="access")
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise unauthorized("Invalid token") from exc

    sub = payload.get("sub")
    if not sub:
        raise unauthorized("Invalid token")
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise unauthorized("Invalid token") from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.status != "confirmed":
        raise unauthorized("User not found")
    return user
