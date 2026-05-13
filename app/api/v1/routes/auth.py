from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.api.v1.deps.db import get_db
from app.core.errors import ErrorCode, RainmakerError, invalid_request
from app.models.user import User
from app.schemas.auth import (
    ConfirmSignUpRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    SignUpRequest,
)
from app.schemas.common import SuccessResponse
from app.services import auth as auth_service
from app.services import email as email_service

router = APIRouter(tags=["auth"])


@router.post("/login2", response_model=LoginResponse)
async def login2(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    # Refresh path: only ``refreshtoken`` provided.
    if payload.refreshtoken and not payload.password:
        access, idt, refresh, exp = await auth_service.login_refresh(
            db, refreshtoken=payload.refreshtoken
        )
        return LoginResponse(accesstoken=access, idtoken=idt, refreshtoken=refresh, expires_in=exp)

    # Password path
    if not payload.user_name or not payload.password:
        raise invalid_request("user_name and password required")

    access, idt, refresh, exp = await auth_service.login_password(
        db, user_name=payload.user_name, password=payload.password
    )
    return LoginResponse(accesstoken=access, idtoken=idt, refreshtoken=refresh, expires_in=exp)


@router.post("/user2", response_model=SuccessResponse)
async def signup(
    payload: SignUpRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    _, code = await auth_service.signup(db, user_name=payload.user_name, password=payload.password)
    await email_service.send_confirmation_email(payload.user_name, code)
    return SuccessResponse(description="Confirmation code sent")


@router.put("/user2", response_model=SuccessResponse)
async def confirm_signup_or_update(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """Dual-purpose endpoint mirroring the SDK:

    * with ``verification_code`` present → confirm signup
    * otherwise → update profile of currently-authenticated user
      (handled by the user router instead — this branch only exists for
      the SDK's `PUT /v1/user2 {user_name, verification_code}` call).
    """
    if "verification_code" in payload and "user_name" in payload:
        try:
            confirm = ConfirmSignUpRequest(**payload)
        except Exception as exc:  # noqa: BLE001
            raise invalid_request("Invalid confirm body") from exc
        await auth_service.confirm_signup(
            db,
            user_name=confirm.user_name,
            verification_code=confirm.verification_code,
        )
        return SuccessResponse(description="User confirmed")

    raise RainmakerError(
        400,
        ErrorCode.INVALID_REQUEST,
        "Missing verification_code (use authed endpoint to update profile)",
    )


@router.put("/password2", response_model=SuccessResponse)
async def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    await auth_service.change_password(
        db, user=user, current=payload.password, new=payload.newpassword
    )
    return SuccessResponse(description="Password changed")


@router.put("/forgotpassword2", response_model=SuccessResponse)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    if payload.verification_code and payload.password:
        await auth_service.confirm_password_reset(
            db,
            user_name=payload.user_name,
            verification_code=payload.verification_code,
            password=payload.password,
        )
        return SuccessResponse(description="Password reset")

    code = await auth_service.request_password_reset(db, user_name=payload.user_name)
    if code is not None:
        await email_service.send_reset_email(payload.user_name, code)
    # Always 200 to avoid leaking account existence.
    return SuccessResponse(description="Reset code sent")


@router.post("/logout2", response_model=SuccessResponse)
async def logout(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    await auth_service.logout(db, user_id=user.id)
    return SuccessResponse(description="Logged out")
