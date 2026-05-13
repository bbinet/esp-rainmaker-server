"""HTTP webhooks served by the `vmq-authz` Deployment for VerneMQ.

The same router is also mounted on the main `api` app so that we can
test these hooks end-to-end with the shared test client. In production
the routes remain reachable only over the internal Service (no Ingress
path exposes them); the dedicated `vmq-authz` Deployment is what
VerneMQ actually talks to via the cluster-internal Service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.db import get_db
from app.services import vmq_authz as authz_service

router = APIRouter(tags=["vmq-authz"])


@router.post("/auth/on_register")
async def on_register(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    return await authz_service.authorise_register(db, username=payload.get("username"))


@router.post("/auth/on_publish")
async def on_publish(payload: dict) -> dict:
    return await authz_service.authorise_publish(
        username=payload.get("username"),
        topic=payload.get("topic", ""),
    )


@router.post("/auth/on_subscribe")
async def on_subscribe(payload: dict) -> dict:
    return await authz_service.authorise_subscribe(
        username=payload.get("username"),
        topics=payload.get("topics"),
    )


@router.post("/on_client_online")
async def on_client_online(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    await authz_service.mark_online(
        db,
        client_id=payload.get("client_id"),
        username=payload.get("username"),
    )
    return {"result": "ok"}


@router.post("/on_client_offline")
async def on_client_offline(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    await authz_service.mark_offline(
        db,
        client_id=payload.get("client_id"),
        username=payload.get("username"),
    )
    return {"result": "ok"}
