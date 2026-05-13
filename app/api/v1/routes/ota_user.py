"""User-facing OTA status read (mobile app)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.api.v1.deps.db import get_db
from app.models.user import User
from app.services import access as access_service
from app.services import ota as ota_service

router = APIRouter(tags=["ota-user"])


@router.get("/user/nodes/ota_status")
async def user_ota_status(
    node_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await access_service.require_access(db, user_id=user.id, node_id=node_id)
    return await ota_service.get_status_for_node(db, node_id=node_id)
