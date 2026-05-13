from __future__ import annotations

from fastapi import Depends

from app.api.v1.deps.auth import get_current_user
from app.core.errors import forbidden
from app.models.user import User


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not (user.is_admin or user.is_super_admin):
        raise forbidden("Admin role required")
    return user
