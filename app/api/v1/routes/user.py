from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.api.v1.deps.db import get_db
from app.models.user import User
from app.schemas.common import SuccessResponse
from app.schemas.user import UserResponse

router = APIRouter(tags=["user"])


@router.get("/user2", response_model=UserResponse)
async def get_current_user_profile(user: User = Depends(get_current_user)) -> User:
    return user


@router.delete("/user2", response_model=SuccessResponse)
async def delete_current_user(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    await db.delete(user)
    await db.commit()
    return SuccessResponse(description="User deleted")
