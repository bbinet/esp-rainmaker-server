"""Node-side business logic: config upsert and params shadow merge.

Both flows have an MQTT mirror (config arrives over `node/<id>/config`
and `params/local` over `node/<id>/params/local`); the HTTPS API is
mostly a fallback for devices unable to maintain MQTT or for boot-time
config sync. We share the persistence path with the MQTT ingestor so
that tests against either surface land on the same state.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.node import NodeConfig, NodeParamsShadow


async def upsert_node_config(
    db: AsyncSession, *, node_id: str, payload: dict[str, Any]
) -> NodeConfig:
    cfg_version = payload.get("config_version")
    existing = (
        await db.execute(select(NodeConfig).where(NodeConfig.node_id == node_id))
    ).scalar_one_or_none()
    if existing is None:
        existing = NodeConfig(node_id=node_id, config_version=cfg_version, payload=payload)
        db.add(existing)
    else:
        existing.config_version = cfg_version
        existing.payload = payload
    await db.commit()
    await db.refresh(existing)
    return existing


def _merge_params(current: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two layers of params (device → {param: value})."""
    merged = dict(current)
    for device_name, params in update.items():
        if isinstance(params, dict) and isinstance(merged.get(device_name), dict):
            merged[device_name] = {**merged[device_name], **params}
        else:
            merged[device_name] = params
    return merged


async def upsert_params_shadow(
    db: AsyncSession, *, node_id: str, params: dict[str, Any]
) -> NodeParamsShadow:
    existing = (
        await db.execute(select(NodeParamsShadow).where(NodeParamsShadow.node_id == node_id))
    ).scalar_one_or_none()
    if existing is None:
        existing = NodeParamsShadow(node_id=node_id, payload=params)
        db.add(existing)
    else:
        existing.payload = _merge_params(existing.payload, params)
    await db.commit()
    await db.refresh(existing)
    return existing
