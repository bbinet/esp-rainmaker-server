"""Node sharing routes (mobile app)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.api.v1.deps.db import get_db
from app.core.errors import invalid_request
from app.models.sharing import NodeSharing
from app.models.user import User
from app.schemas.common import SuccessResponse
from app.services import sharing as sharing_service

router = APIRouter(tags=["sharing"])


@router.put("/user/nodes/sharing/requests")
async def put_sharing_request(
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Two shapes: owner-initiated (nodes + user_name) or invitee accept
    # (request_id + accept).
    if "request_id" in payload:
        try:
            rid = uuid.UUID(payload["request_id"])
        except (TypeError, ValueError) as exc:
            raise invalid_request("invalid request_id") from exc
        return await sharing_service.accept_or_decline(
            db,
            invitee_id=user.id,
            invitee_name=user.user_name,
            request_id=rid,
            accept=bool(payload.get("accept", True)),
        )

    nodes = payload.get("nodes") or []
    to_user_name = payload.get("user_name")
    if not nodes or not to_user_name:
        raise invalid_request("nodes and user_name required")
    request_id = await sharing_service.create_request(
        db, from_user_id=user.id, node_ids=list(nodes), to_user_name=to_user_name
    )
    return {"status": "success", "request_id": request_id}


@router.get("/user/nodes/sharing")
async def list_sharing(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    granted_to = (
        (await db.execute(select(NodeSharing).where(NodeSharing.to_user_id == user.id)))
        .scalars()
        .all()
    )
    granted_from = (
        (await db.execute(select(NodeSharing).where(NodeSharing.from_user_id == user.id)))
        .scalars()
        .all()
    )
    return {
        "shared_with_me": [{"node_id": s.node_id, "role": s.role} for s in granted_to],
        "shared_by_me": [
            {"node_id": s.node_id, "to_user_id": str(s.to_user_id)} for s in granted_from
        ],
    }


@router.delete("/user/nodes/sharing", response_model=SuccessResponse)
async def revoke_sharing(
    nodes: str = Query(...),
    user_name: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    node_ids = [n for n in nodes.split(",") if n]
    await sharing_service.revoke_share(
        db, owner_id=user.id, node_ids=node_ids, invitee_name=user_name
    )
    return SuccessResponse(description="Sharing revoked")
