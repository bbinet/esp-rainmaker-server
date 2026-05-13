"""Automation CRUD routes (mobile app)."""

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
from app.services import automations as automations_service

router = APIRouter(tags=["automations"])


@router.post("/user/node_automation")
async def create_automation(
    payload: dict[str, Any],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    name = payload.get("name")
    if not name:
        raise invalid_request("name required")
    row = await automations_service.create_automation(
        db,
        user_id=user.id,
        name=name,
        events=payload.get("events") or [],
        actions=payload.get("actions") or [],
        event_operator=payload.get("event_operator", "and"),
        metadata=payload.get("metadata"),
    )
    return {"status": "success", "automation_id": str(row.id)}


@router.get("/user/node_automation")
async def list_automations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = await automations_service.list_automations(db, user_id=user.id)
    return {"automations": [automations_service.serialise(r) for r in rows]}


@router.put("/user/node_automation", response_model=SuccessResponse)
async def update_automation(
    payload: dict[str, Any],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    try:
        automation_id = uuid.UUID(payload["automation_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise invalid_request("automation_id required") from exc
    await automations_service.update_automation(
        db, user_id=user.id, automation_id=automation_id, patch=payload
    )
    return SuccessResponse(description="Automation updated")


@router.delete("/user/node_automation", response_model=SuccessResponse)
async def delete_automation(
    automation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    try:
        aid = uuid.UUID(automation_id)
    except (TypeError, ValueError) as exc:
        raise invalid_request("invalid automation_id") from exc
    await automations_service.delete_automation(db, user_id=user.id, automation_id=aid)
    return SuccessResponse(description="Automation deleted")
