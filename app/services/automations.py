"""Automation CRUD.

Automation evaluation (trigger on params/local change, fire actions
publishing to node/<id>/params/remote) lands in the worker (Phase 8.5);
this module is the storage + API surface only.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RainmakerError
from app.models.automation import Automation


async def create_automation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    events: list[Any],
    actions: list[Any],
    event_operator: str = "and",
    metadata: dict[str, Any] | None = None,
) -> Automation:
    row = Automation(
        user_id=user_id,
        name=name,
        events=events or [],
        actions=actions or [],
        event_operator=event_operator,
        metadata_=metadata or {},
        enabled=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_automations(db: AsyncSession, *, user_id: uuid.UUID) -> list[Automation]:
    rows = (
        (await db.execute(select(Automation).where(Automation.user_id == user_id))).scalars().all()
    )
    return list(rows)


async def update_automation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    automation_id: uuid.UUID,
    patch: dict[str, Any],
) -> Automation:
    row = (
        await db.execute(
            select(Automation).where(Automation.id == automation_id, Automation.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise RainmakerError(404, 100015, "Automation not found")
    if "name" in patch:
        row.name = patch["name"]
    if "enabled" in patch:
        row.enabled = bool(patch["enabled"])
    if "event_operator" in patch:
        row.event_operator = patch["event_operator"]
    if "events" in patch:
        row.events = patch["events"] or []
    if "actions" in patch:
        row.actions = patch["actions"] or []
    if "metadata" in patch:
        row.metadata_ = patch["metadata"] or {}
    await db.commit()
    await db.refresh(row)
    return row


async def delete_automation(
    db: AsyncSession, *, user_id: uuid.UUID, automation_id: uuid.UUID
) -> None:
    row = (
        await db.execute(
            select(Automation).where(Automation.id == automation_id, Automation.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise RainmakerError(404, 100015, "Automation not found")
    await db.delete(row)
    await db.commit()


def serialise(row: Automation) -> dict[str, Any]:
    return {
        "automation_id": str(row.id),
        "name": row.name,
        "enabled": row.enabled,
        "event_operator": row.event_operator,
        "events": row.events,
        "actions": row.actions,
        "metadata": row.metadata_ or {},
    }
