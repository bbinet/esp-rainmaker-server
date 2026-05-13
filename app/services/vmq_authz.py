"""Authorisation logic answering VerneMQ vmq_webhooks hooks.

Decision model:

* **auth_on_register** —
  - mTLS listener: VerneMQ has verified the device's cert against our
    trust chain and propagated the CN as the MQTT username. We accept
    the connect when ``node_certificates`` has an active row for that CN.
  - Plain TCP listener (compose-network only): backend services connect
    with ``RM_MQTT_INTERNAL_USER``; we accept that username unconditionally.
* **auth_on_publish / on_subscribe** — devices are scoped to
  ``node/<cn>/...``; the backend internal user can publish/subscribe
  anywhere.
* **on_client_online / offline** — informational; updates
  ``nodes.online`` and ``nodes.last_seen_at`` for device CNs only.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.node import Node, NodeCertificate


def _is_backend(username: str | None) -> bool:
    if not username:
        return False
    return username == get_settings().mqtt_internal_user


async def authorise_register(db: AsyncSession, *, username: str | None) -> dict:
    if not username:
        return {"result": {"error": "missing username"}}
    if _is_backend(username):
        return {"result": "ok"}
    cert = (
        (
            await db.execute(
                select(NodeCertificate)
                .where(
                    NodeCertificate.cn == username,
                    NodeCertificate.revoked.is_(False),
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if cert is None:
        return {"result": {"error": "unknown or revoked client"}}
    return {"result": "ok"}


def _allowed_topic(username: str, topic: str) -> bool:
    prefix = f"node/{username}/"
    return topic.startswith(prefix)


async def authorise_publish(*, username: str | None, topic: str) -> dict:
    if not topic:
        return {"result": {"error": "missing topic"}}
    if _is_backend(username):
        return {"result": "ok"}
    if not username:
        return {"result": {"error": "missing username"}}
    if not _allowed_topic(username, topic):
        return {"result": {"error": "topic outside namespace"}}
    return {"result": "ok"}


async def authorise_subscribe(*, username: str | None, topics: list[dict] | None) -> dict:
    if _is_backend(username):
        return {"result": "ok"}
    if not username:
        return {"result": {"error": "missing username"}}
    if not topics:
        return {"result": "ok"}
    for t in topics:
        topic = t.get("topic", "")
        if not _allowed_topic(username, topic):
            return {"result": {"error": f"topic outside namespace: {topic}"}}
    return {"result": "ok"}


async def mark_online(db: AsyncSession, *, client_id: str | None, username: str | None) -> None:
    if _is_backend(username):
        return
    node_id = username or client_id
    if not node_id:
        return
    now = datetime.now(UTC)
    await db.execute(
        update(Node).where(Node.node_id == node_id).values(online=True, last_seen_at=now)
    )
    await db.commit()


async def mark_offline(db: AsyncSession, *, client_id: str | None, username: str | None) -> None:
    if _is_backend(username):
        return
    node_id = username or client_id
    if not node_id:
        return
    now = datetime.now(UTC)
    await db.execute(
        update(Node).where(Node.node_id == node_id).values(online=False, last_seen_at=now)
    )
    await db.commit()
