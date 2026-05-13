"""App-facing node management routes (JWT)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.api.v1.deps.db import get_db
from app.core.errors import ErrorCode, RainmakerError, invalid_request
from app.models.node import Node, NodeConfig, NodeParamsShadow
from app.models.user import User
from app.models.user_node import UserNodeMapping
from app.mqtt.publisher import publish_node_topic
from app.mqtt.topics import PARAMS_REMOTE
from app.schemas.common import SuccessResponse
from app.services import access as access_service
from app.services import mapping as mapping_service

router = APIRouter(tags=["user-nodes"])


# ---------------- Nodes list / details ----------------


@router.get("/user/nodes")
async def list_user_nodes(
    node_details: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    node_ids = await access_service.accessible_node_ids(db, user_id=user.id)
    response: dict = {"nodes": node_ids, "total": len(node_ids)}
    if node_details:
        details = []
        for nid in node_ids:
            node = (await db.execute(select(Node).where(Node.node_id == nid))).scalar_one_or_none()
            if node is None:
                continue
            details.append(
                {
                    "id": nid,
                    "type": node.node_type,
                    "fw_version": node.fw_version,
                    "model": node.model,
                    "online": node.online,
                    "tags": node.tags or [],
                }
            )
        response["node_details"] = details
    return response


@router.delete("/user/nodes/{node_id}", response_model=SuccessResponse)
async def delete_user_node(
    node_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    mapping = (
        await db.execute(
            select(UserNodeMapping).where(
                UserNodeMapping.user_id == user.id,
                UserNodeMapping.node_id == node_id,
            )
        )
    ).scalar_one_or_none()
    if mapping is None:
        raise RainmakerError(404, ErrorCode.NODE_NOT_MAPPED, "Node not mapped to this user")
    await db.delete(mapping)
    await db.commit()
    return SuccessResponse(description="Node unmapped")


# ---------------- Config / status / params ----------------


@router.get("/user/nodes/config")
async def get_user_node_config(
    node_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await access_service.require_access(db, user_id=user.id, node_id=node_id)
    cfg = (
        await db.execute(select(NodeConfig).where(NodeConfig.node_id == node_id))
    ).scalar_one_or_none()
    if cfg is None:
        return {"node_id": node_id, "config_version": None, "payload": {}}
    return {
        "node_id": node_id,
        "config_version": cfg.config_version,
        **cfg.payload,
    }


@router.get("/user/nodes/status")
async def get_user_node_status(
    node_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await access_service.require_access(db, user_id=user.id, node_id=node_id)
    node = (await db.execute(select(Node).where(Node.node_id == node_id))).scalar_one_or_none()
    if node is None:
        raise RainmakerError(404, ErrorCode.NODES_DO_NOT_EXIST, "Node not found")
    return {
        "node_id": node_id,
        "connectivity": {
            "connected": node.online,
            "timestamp": node.last_seen_at.isoformat() if node.last_seen_at else None,
        },
    }


@router.get("/user/nodes/params")
async def get_user_node_params(
    node_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await access_service.require_access(db, user_id=user.id, node_id=node_id)
    shadow = (
        await db.execute(select(NodeParamsShadow).where(NodeParamsShadow.node_id == node_id))
    ).scalar_one_or_none()
    return shadow.payload if shadow else {}


@router.put("/user/nodes/params", response_model=SuccessResponse)
async def put_user_node_params(
    payload: dict,
    node_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    await access_service.require_access(db, user_id=user.id, node_id=node_id)
    # The shadow is "reported" state owned by the device — we don't pre-
    # update it here. Instead we publish the desired update; the device
    # echoes back on params/local and the ingestor (Phase 4) merges.
    await publish_node_topic(node_id=node_id, suffix=PARAMS_REMOTE, payload=payload)
    return SuccessResponse(description="Params published")


# ---------------- Mapping ----------------


@router.put("/user/nodes/mapping")
async def put_user_node_mapping(
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    node_id = payload.get("node_id")
    secret_key = payload.get("secret_key")
    operation = payload.get("operation", "add")
    if not node_id or not secret_key:
        raise invalid_request("node_id and secret_key required")
    request_id = await mapping_service.request_legacy_mapping(
        db,
        user_id=user.id,
        node_id=node_id,
        secret_key=secret_key,
        operation=operation,
    )
    return {"status": "success", "request_id": request_id}


@router.get("/user/nodes/mapping")
async def get_user_node_mapping(
    request_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await mapping_service.get_mapping_status(db, user_id=user.id, request_id=request_id)


@router.post("/user/nodes/mapping/initiate")
async def initiate_mapping(
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    node_id = payload.get("node_id")
    if not node_id:
        raise invalid_request("node_id required")
    return await mapping_service.initiate_challenge(db, user_id=user.id, node_id=node_id)


@router.post("/user/nodes/mapping/verify")
async def verify_mapping(
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    request_id = payload.get("request_id")
    challenge_response = payload.get("challenge_response")
    if not request_id or not challenge_response:
        raise invalid_request("request_id and challenge_response required")
    return await mapping_service.verify_challenge(
        db,
        user_id=user.id,
        request_id=request_id,
        challenge_response=challenge_response,
    )


# ---------------- custom_data ----------------


@router.get("/user/custom_data")
async def get_custom_data(user: User = Depends(get_current_user)) -> dict:
    return user.custom_data or {}


@router.put("/user/custom_data", response_model=SuccessResponse)
async def put_custom_data(
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    user.custom_data = payload
    await db.commit()
    return SuccessResponse(description="Custom data stored")
