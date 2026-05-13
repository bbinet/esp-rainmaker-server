"""JWT minting/verification and password hashing.

The RainMaker phone app sends the raw access token as `Authorization`
header (no `Bearer` prefix). The login endpoint returns three tokens
in lowercase keys: `accesstoken`, `idtoken`, `refreshtoken`.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TokenKind = Literal["access", "id", "refresh"]


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def _ttl_for(kind: TokenKind) -> int:
    s = get_settings()
    return {
        "access": s.access_token_ttl_seconds,
        "id": s.id_token_ttl_seconds,
        "refresh": s.refresh_token_ttl_seconds,
    }[kind]


def mint_token(
    subject: str,
    kind: TokenKind,
    *,
    extra_claims: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = ttl_seconds if ttl_seconds is not None else _ttl_for(kind)
    exp = now + timedelta(seconds=ttl)
    claims: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "token_use": kind,
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        claims.update(extra_claims)
    token = jwt.encode(
        claims,
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, exp


def decode_token(token: str, *, expected_kind: TokenKind | None = None) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )
    if expected_kind is not None and payload.get("token_use") != expected_kind:
        raise jwt.InvalidTokenError(f"Expected {expected_kind} token, got {payload.get('token_use')}")
    return payload


def generate_secret(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)
