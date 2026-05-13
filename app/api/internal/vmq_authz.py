"""HTTP webhooks served by the `vmq-authz` Deployment for VerneMQ.

The same router is also mounted on the main `api` app so that we can
test these hooks end-to-end with the shared test client. In production
the routes remain reachable only over the internal Service (no Ingress
path exposes them); the dedicated `vmq-authz` Deployment is what
VerneMQ actually talks to via the cluster-internal Service.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.db import get_db
from app.core.logging import get_logger
from app.services import vmq_authz as authz_service

router = APIRouter(tags=["vmq-authz"])
_logger = get_logger(__name__)


@router.post("/auth/on_register")
async def on_register(
    payload: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    result = await authz_service.authorise_register(db, username=payload.get("username"))
    _logger.info(
        "vmq_authz_on_register",
        username=payload.get("username"),
        client_id=payload.get("client_id"),
        result=result,
    )
    return result


@router.post("/auth/on_publish")
async def on_publish(payload: dict[str, Any]) -> dict[str, Any]:
    result = await authz_service.authorise_publish(
        username=payload.get("username"),
        topic=payload.get("topic", ""),
    )
    _logger.info(
        "vmq_authz_on_publish",
        username=payload.get("username"),
        topic=payload.get("topic"),
        qos=payload.get("qos"),
        result=result,
    )
    return result


@router.post("/auth/on_subscribe")
async def on_subscribe(payload: dict[str, Any]) -> dict[str, Any]:
    result = await authz_service.authorise_subscribe(
        username=payload.get("username"),
        topics=payload.get("topics"),
    )
    _logger.info(
        "vmq_authz_on_subscribe",
        username=payload.get("username"),
        topics=payload.get("topics"),
        result=result,
    )
    return result


@router.post("/on_client_online")
async def on_client_online(
    payload: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    await authz_service.mark_online(
        db,
        client_id=payload.get("client_id"),
        username=payload.get("username"),
    )
    return {"result": "ok"}


@router.post("/on_client_offline")
async def on_client_offline(
    payload: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    await authz_service.mark_offline(
        db,
        client_id=payload.get("client_id"),
        username=payload.get("username"),
    )
    return {"result": "ok"}
