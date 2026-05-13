"""Node group routes (mobile app)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.api.v1.deps.db import get_db
from app.core.errors import invalid_request
from app.models.user import User
from app.schemas.common import SuccessResponse
from app.services import groups as groups_service

router = APIRouter(tags=["groups"])


@router.post("/user/node_group")
async def create_group(
    payload: dict[str, Any],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    name = payload.get("group_name")
    if not name:
        raise invalid_request("group_name required")
    parent_str = payload.get("parent_group_id")
    parent_id: uuid.UUID | None = None
    if parent_str:
        try:
            parent_id = uuid.UUID(parent_str)
        except (TypeError, ValueError) as exc:
            raise invalid_request("invalid parent_group_id") from exc

    group = await groups_service.create_group(
        db,
        owner_id=user.id,
        name=name,
        parent_group_id=parent_id,
        group_type=payload.get("type"),
        mutually_exclusive=bool(payload.get("mutually_exclusive", False)),
        nodes=payload.get("nodes") or [],
        metadata=payload.get("metadata"),
    )
    return {"status": "success", "group_id": str(group.id)}


@router.get("/user/node_group")
async def list_user_node_groups(
    group_id: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    gid = uuid.UUID(group_id) if group_id else None
    groups = await groups_service.list_groups(db, owner_id=user.id, group_id=gid)
    return {"groups": groups}


@router.delete("/user/node_group/{group_id}", response_model=SuccessResponse)
async def delete_user_node_group(
    group_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    try:
        gid = uuid.UUID(group_id)
    except (TypeError, ValueError) as exc:
        raise invalid_request("invalid group_id") from exc
    await groups_service.delete_group(db, owner_id=user.id, group_id=gid)
    return SuccessResponse(description="Group deleted")
