"""Node-facing HTTPS endpoints (mTLS).

Identified by ``X-SSL-Client-CN`` injected by the mTLS Ingress.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.db import get_db
from app.api.v1.deps.mtls import get_node_from_mtls
from app.core.errors import invalid_request
from app.models.node import Node
from app.schemas.common import SuccessResponse
from app.services import node as node_service
from app.services import ota as ota_service

router = APIRouter(tags=["node"])


@router.put("/node/config", response_model=SuccessResponse)
async def put_node_config(
    payload: dict,
    node: Node = Depends(get_node_from_mtls),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    await node_service.upsert_node_config(db, node_id=node.node_id, payload=payload)
    return SuccessResponse(description="Config stored")


@router.post("/node/params/local", response_model=SuccessResponse)
@router.put("/node/params/local", response_model=SuccessResponse)
async def post_node_params_local(
    payload: dict,
    node: Node = Depends(get_node_from_mtls),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    await node_service.upsert_params_shadow(db, node_id=node.node_id, params=payload)
    return SuccessResponse(description="Params merged")


@router.get("/node/otafetch")
async def get_node_otafetch(
    node: Node = Depends(get_node_from_mtls),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await ota_service.fetch_pending_for_node(db, node_id=node.node_id)


@router.post("/node/otastatus", response_model=SuccessResponse)
async def post_node_otastatus(
    payload: dict,
    node: Node = Depends(get_node_from_mtls),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    job_id = payload.get("ota_job_id")
    status_value = payload.get("status")
    if not job_id or not status_value:
        raise invalid_request("ota_job_id and status required")
    await ota_service.update_status(
        db,
        node_id=node.node_id,
        ota_job_id=job_id,
        status=status_value,
        additional_info=payload.get("additional_info"),
    )
    return SuccessResponse(description="OTA status recorded")
