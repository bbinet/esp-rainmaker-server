"""Node-facing HTTPS endpoints (mTLS).

Identified by ``X-SSL-Client-CN`` injected by the mTLS Ingress.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.db import get_db
from app.api.v1.deps.mtls import get_node_from_mtls
from app.core.errors import RainmakerError
from app.models.node import Node
from app.schemas.common import SuccessResponse
from app.services import node as node_service

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
    """Return the pending OTA job for this node, or a no-op `wait`.

    Phase 6 fills in the actual job-lookup logic; until then we tell the
    firmware to back off so it doesn't busy-poll.
    """
    _ = (node, db)
    return {
        "ota_available": False,
        "action": "wait",
        "min_wait": 60,
        "max_wait": 600,
    }


@router.post("/node/otastatus", response_model=SuccessResponse)
async def post_node_otastatus(
    payload: dict,
    node: Node = Depends(get_node_from_mtls),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """Record an OTA status report.

    Phase 6 wires this against `ota_job_nodes`. Until then, if there is
    no known job for the (node, ota_job_id) pair we return 404.
    """
    _ = (node, db)
    raise RainmakerError(404, 105012, "OTA job not found")  # ErrorCode.OTA_JOB_NOT_FOUND


# Stable export name (so the v1_router include is symmetric with other routes).
