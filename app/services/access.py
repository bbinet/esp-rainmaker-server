"""Per-node access policy.

A user can act on a node when they own it (primary mapping) or have an
active sharing entry (added in Phase 7). For now only the primary path
is wired; sharing checks are added later without touching call sites.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden
from app.models.user_node import UserNodeMapping


async def has_access(db: AsyncSession, *, user_id: uuid.UUID, node_id: str) -> bool:
    row = (
        await db.execute(
            select(UserNodeMapping).where(
                UserNodeMapping.user_id == user_id,
                UserNodeMapping.node_id == node_id,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def require_access(db: AsyncSession, *, user_id: uuid.UUID, node_id: str) -> None:
    if not await has_access(db, user_id=user_id, node_id=node_id):
        raise forbidden("Node not mapped to this user")
