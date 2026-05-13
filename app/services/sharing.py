"""Node sharing flow.

Owner ``PUT /v1/user/nodes/sharing/requests {nodes, user_name}`` creates
a pending request. Invitee ``PUT /v1/user/nodes/sharing/requests
{request_id, accept}`` either accepts (turning the request into a
NodeSharing row) or declines.

NodeSharing entries are the secondary mappings consumed by
``app.services.access.has_access``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RainmakerError, invalid_request
from app.models.sharing import NodeSharing, NodeSharingRequest
from app.models.user import User
from app.models.user_node import UserNodeMapping


def _now() -> datetime:
    return datetime.now(UTC)


async def create_request(
    db: AsyncSession,
    *,
    from_user_id: uuid.UUID,
    node_ids: list[str],
    to_user_name: str,
) -> str:
    """Owner-initiated invite. Returns the request_id."""
    if not node_ids or not to_user_name:
        raise invalid_request("nodes and user_name required")

    # Ensure the owner actually owns every node.
    for nid in node_ids:
        owns = (
            await db.execute(
                select(UserNodeMapping).where(
                    UserNodeMapping.user_id == from_user_id,
                    UserNodeMapping.node_id == nid,
                )
            )
        ).scalar_one_or_none()
        if owns is None:
            raise RainmakerError(403, 100070, f"Not owner of node {nid}")

    # One request row per (node, invitee) — collapsed here as one row
    # but the SDK exposes a single request_id, so we treat the first node
    # as the row's identity and link the others through metadata.
    primary_node = node_ids[0]
    row = NodeSharingRequest(
        node_id=primary_node,
        from_user_id=from_user_id,
        to_user_name=to_user_name,
        status="pending",
        expires_at=_now() + timedelta(days=7),
    )
    db.add(row)
    for extra in node_ids[1:]:
        db.add(
            NodeSharingRequest(
                node_id=extra,
                from_user_id=from_user_id,
                to_user_name=to_user_name,
                status="pending",
                expires_at=_now() + timedelta(days=7),
            )
        )
    await db.commit()
    await db.refresh(row)
    return str(row.id)


async def accept_or_decline(
    db: AsyncSession,
    *,
    invitee_id: uuid.UUID,
    invitee_name: str,
    request_id: uuid.UUID,
    accept: bool,
) -> dict:
    row = (
        await db.execute(select(NodeSharingRequest).where(NodeSharingRequest.id == request_id))
    ).scalar_one_or_none()
    if row is None:
        raise RainmakerError(404, 100015, "Request not found")
    if row.to_user_name != invitee_name:
        raise RainmakerError(403, 100070, "Request is for a different user")
    if row.status != "pending":
        raise invalid_request("Request already resolved")
    if row.expires_at < _now():
        row.status = "expired"
        await db.commit()
        raise invalid_request("Request expired")

    if accept:
        row.status = "accepted"
        existing = (
            await db.execute(
                select(NodeSharing).where(
                    NodeSharing.node_id == row.node_id,
                    NodeSharing.to_user_id == invitee_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                NodeSharing(
                    node_id=row.node_id,
                    from_user_id=row.from_user_id,
                    to_user_id=invitee_id,
                    role="secondary",
                )
            )
    else:
        row.status = "declined"
    await db.commit()
    return {"status": "success", "request_status": row.status}


async def revoke_share(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    node_ids: list[str],
    invitee_name: str,
) -> None:
    invitee = (
        await db.execute(select(User).where(User.user_name == invitee_name))
    ).scalar_one_or_none()
    if invitee is None:
        return
    for nid in node_ids:
        share = (
            await db.execute(
                select(NodeSharing).where(
                    NodeSharing.node_id == nid,
                    NodeSharing.from_user_id == owner_id,
                    NodeSharing.to_user_id == invitee.id,
                )
            )
        ).scalar_one_or_none()
        if share is not None:
            await db.delete(share)
    await db.commit()
