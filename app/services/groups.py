"""Node group business logic.

Groups are user-scoped, optionally hierarchical (``parent_id``) and may
declare ``mutually_exclusive`` semantics that downstream features (UI,
automation policies) can honour. Membership is captured in
``node_group_nodes``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RainmakerError, invalid_request
from app.models.sharing import NodeGroup, NodeGroupNode


async def create_group(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    name: str,
    parent_group_id: uuid.UUID | None = None,
    group_type: str | None = None,
    mutually_exclusive: bool = False,
    nodes: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> NodeGroup:
    if not name:
        raise invalid_request("group_name required")
    if parent_group_id is not None:
        parent = (
            await db.execute(
                select(NodeGroup).where(
                    NodeGroup.id == parent_group_id, NodeGroup.owner_id == owner_id
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise invalid_request("Unknown parent group")

    group = NodeGroup(
        owner_id=owner_id,
        parent_id=parent_group_id,
        name=name,
        type=group_type,
        mutually_exclusive=mutually_exclusive,
        metadata_=metadata or {},
    )
    db.add(group)
    await db.flush()
    if nodes:
        for nid in nodes:
            db.add(NodeGroupNode(group_id=group.id, node_id=nid))
    await db.commit()
    await db.refresh(group)
    return group


async def list_groups(
    db: AsyncSession, *, owner_id: uuid.UUID, group_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    query = select(NodeGroup).where(NodeGroup.owner_id == owner_id)
    if group_id is not None:
        query = query.where(NodeGroup.id == group_id)
    rows = (await db.execute(query)).scalars().all()
    out: list[dict[str, Any]] = []
    for g in rows:
        node_ids = (
            (await db.execute(select(NodeGroupNode.node_id).where(NodeGroupNode.group_id == g.id)))
            .scalars()
            .all()
        )
        out.append(
            {
                "group_id": str(g.id),
                "group_name": g.name,
                "type": g.type,
                "mutually_exclusive": g.mutually_exclusive,
                "parent_group_id": str(g.parent_id) if g.parent_id else None,
                "nodes": list(node_ids),
            }
        )
    return out


async def delete_group(db: AsyncSession, *, owner_id: uuid.UUID, group_id: uuid.UUID) -> None:
    group = (
        await db.execute(
            select(NodeGroup).where(NodeGroup.id == group_id, NodeGroup.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    if group is None:
        raise RainmakerError(404, 100015, "Group not found")
    await db.delete(group)
    await db.commit()
