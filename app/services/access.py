"""Per-node access policy.

A user can act on a node when they own it (primary mapping) or have an
active NodeSharing entry granted by another owner.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden
from app.models.sharing import NodeSharing
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
    if row is not None:
        return True
    share = (
        await db.execute(
            select(NodeSharing).where(
                NodeSharing.to_user_id == user_id,
                NodeSharing.node_id == node_id,
            )
        )
    ).scalar_one_or_none()
    return share is not None


async def require_access(db: AsyncSession, *, user_id: uuid.UUID, node_id: str) -> None:
    if not await has_access(db, user_id=user_id, node_id=node_id):
        raise forbidden("Node not mapped to this user")


async def accessible_node_ids(db: AsyncSession, *, user_id: uuid.UUID) -> list[str]:
    """Return the union of owned + shared node_ids for a user."""
    owned = (
        (
            await db.execute(
                select(UserNodeMapping.node_id).where(UserNodeMapping.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    shared = (
        (await db.execute(select(NodeSharing.node_id).where(NodeSharing.to_user_id == user_id)))
        .scalars()
        .all()
    )
    return list(dict.fromkeys(list(owned) + list(shared)))
