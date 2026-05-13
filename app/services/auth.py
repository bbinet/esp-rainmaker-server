"""Authentication service.

Owns the user lifecycle (signup → confirm → login → refresh → logout)
and the JWT triple (access / id / refresh) returned to the mobile app.

Wire format quirks intentionally preserved for SDK compatibility:

* response keys are lowercase: ``accesstoken``, ``idtoken``, ``refreshtoken``
* ``POST /v1/login2`` handles both password and refresh-token bodies
* ``Authorization: <jwt>`` — no ``Bearer`` prefix
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ErrorCode, RainmakerError, conflict, unauthorized
from app.core.security import (
    decode_token,
    hash_password,
    mint_token,
    verify_password,
)
from app.models.user import RefreshToken, User


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def signup(db: AsyncSession, *, user_name: str, password: str) -> tuple[User, str]:
    """Create an unconfirmed user. Returns (user, confirmation_code)."""
    existing = (
        await db.execute(select(User).where(User.user_name == user_name))
    ).scalar_one_or_none()
    if existing:
        raise conflict("User already exists", code=ErrorCode.USER_ALREADY_EXISTS)

    code = _generate_code()
    user = User(
        user_name=user_name,
        email=user_name,
        password_hash=hash_password(password),
        status="unconfirmed",
        confirm_code=code,
        confirm_code_exp=_now() + timedelta(hours=24),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user, code


async def confirm_signup(db: AsyncSession, *, user_name: str, verification_code: str) -> User:
    user = (await db.execute(select(User).where(User.user_name == user_name))).scalar_one_or_none()
    if user is None:
        raise RainmakerError(404, ErrorCode.USER_DOES_NOT_EXIST, "User does not exist")
    if user.confirm_code is None or user.confirm_code_exp is None:
        raise RainmakerError(400, ErrorCode.INVALID_REQUEST, "No pending confirmation")
    if user.confirm_code_exp < _now():
        raise RainmakerError(400, ErrorCode.INVALID_REQUEST, "Confirmation code expired")
    if not secrets.compare_digest(user.confirm_code, verification_code):
        raise RainmakerError(400, ErrorCode.INVALID_REQUEST, "Invalid verification code")

    user.status = "confirmed"
    user.confirm_code = None
    user.confirm_code_exp = None
    await db.commit()
    await db.refresh(user)
    return user


async def login_password(
    db: AsyncSession, *, user_name: str, password: str
) -> tuple[str, str, str, int]:
    """Verify password and mint (access, id, refresh, expires_in)."""
    user = (await db.execute(select(User).where(User.user_name == user_name))).scalar_one_or_none()
    if user is None or user.password_hash is None:
        raise unauthorized("Invalid credentials")
    if not verify_password(password, user.password_hash):
        raise unauthorized("Invalid credentials")
    if user.status != "confirmed":
        raise RainmakerError(401, ErrorCode.INVALID_CREDENTIALS, "User not confirmed")

    user.last_login_at = _now()
    await db.commit()
    return await _mint_token_triple(db, user)


async def login_refresh(db: AsyncSession, *, refreshtoken: str) -> tuple[str, str, str, int]:
    """Validate a refresh token and mint new access+id tokens.

    The refresh token itself is reused (not rotated) for simplicity — the
    SDK accepts either behaviour.
    """
    try:
        payload = decode_token(refreshtoken, expected_kind="refresh")
    except Exception as exc:  # noqa: BLE001
        raise unauthorized("Invalid refresh token") from exc

    jti = payload.get("jti")
    sub = payload.get("sub")
    if not jti or not sub:
        raise unauthorized("Invalid refresh token")

    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.jti == jti))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at < _now():
        raise unauthorized("Invalid refresh token")

    user = (await db.execute(select(User).where(User.id == uuid.UUID(sub)))).scalar_one_or_none()
    if user is None or user.status != "confirmed":
        raise unauthorized("Invalid refresh token")

    settings = get_settings()
    access, _ = mint_token(str(user.id), "access", extra_claims={"role": _role(user)})
    idt, _ = mint_token(
        str(user.id),
        "id",
        extra_claims={"user_name": user.user_name, "email": user.email},
    )
    return access, idt, refreshtoken, settings.access_token_ttl_seconds


async def logout(db: AsyncSession, *, user_id: uuid.UUID) -> None:
    """Revoke every active refresh token for the user."""
    rows = (
        (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    now = _now()
    for r in rows:
        r.revoked_at = now
    await db.commit()


async def change_password(db: AsyncSession, *, user: User, current: str, new: str) -> None:
    if user.password_hash is None or not verify_password(current, user.password_hash):
        raise unauthorized("Invalid credentials")
    user.password_hash = hash_password(new)
    await db.commit()


async def request_password_reset(db: AsyncSession, *, user_name: str) -> str | None:
    """Returns the reset code (for tests) — in production it's emailed."""
    user = (await db.execute(select(User).where(User.user_name == user_name))).scalar_one_or_none()
    if user is None:
        # Do not leak existence: the route still returns 200.
        return None
    code = _generate_code()
    user.reset_code = code
    user.reset_code_exp = _now() + timedelta(hours=1)
    await db.commit()
    return code


async def confirm_password_reset(
    db: AsyncSession, *, user_name: str, verification_code: str, password: str
) -> None:
    user = (await db.execute(select(User).where(User.user_name == user_name))).scalar_one_or_none()
    if (
        user is None
        or user.reset_code is None
        or user.reset_code_exp is None
        or user.reset_code_exp < _now()
        or not secrets.compare_digest(user.reset_code, verification_code)
    ):
        raise RainmakerError(400, ErrorCode.INVALID_REQUEST, "Invalid or expired code")

    user.password_hash = hash_password(password)
    user.reset_code = None
    user.reset_code_exp = None
    await db.commit()


async def _mint_token_triple(db: AsyncSession, user: User) -> tuple[str, str, str, int]:
    settings = get_settings()
    role = _role(user)

    access, _ = mint_token(str(user.id), "access", extra_claims={"role": role})
    idt, _ = mint_token(
        str(user.id),
        "id",
        extra_claims={"user_name": user.user_name, "email": user.email},
    )
    refresh, refresh_exp = mint_token(str(user.id), "refresh")
    payload = decode_token(refresh, expected_kind="refresh")
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=payload["jti"],
            expires_at=refresh_exp,
        )
    )
    await db.commit()
    return access, idt, refresh, settings.access_token_ttl_seconds


def _role(user: User) -> str:
    if user.is_super_admin:
        return "super_admin"
    if user.is_admin:
        return "admin"
    return "user"
