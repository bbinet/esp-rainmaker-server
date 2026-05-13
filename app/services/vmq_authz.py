"""Authorisation logic answering VerneMQ vmq_webhooks hooks.

Decision model:

* **auth_on_register** — VerneMQ has already verified the device's mTLS
  cert against the trust chain. We then check the CN (= node_id) is
  present in ``node_certificates`` and not revoked. The MQTT username
  is set to the cert's CN via VerneMQ's ``use_identity_as_username``.
* **auth_on_publish / on_subscribe** — only topics under
  ``node/<username>/...`` are allowed. Other namespaces (admin, fan-out)
  are reserved for backend clients with dedicated credentials.
* **on_client_online / offline** — informational; updates ``nodes.online``
  and ``nodes.last_seen_at``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.node import Node, NodeCertificate


async def authorise_register(db: AsyncSession, *, username: str | None) -> dict:
    if not username:
        return {"result": {"error": "missing username"}}
    cert = (
        await db.execute(
            select(NodeCertificate).where(
                NodeCertificate.cn == username,
                NodeCertificate.revoked.is_(False),
            )
        )
    ).scalar_one_or_none()
    if cert is None:
        return {"result": {"error": "unknown or revoked client"}}
    return {"result": "ok"}


def _allowed_topic(username: str, topic: str) -> bool:
    prefix = f"node/{username}/"
    return topic.startswith(prefix)


async def authorise_publish(*, username: str | None, topic: str) -> dict:
    if not username or not topic:
        return {"result": {"error": "missing fields"}}
    if not _allowed_topic(username, topic):
        return {"result": {"error": "topic outside namespace"}}
    return {"result": "ok"}


async def authorise_subscribe(*, username: str | None, topics: list[dict] | None) -> dict:
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
    node_id = username or client_id
    if not node_id:
        return
    now = datetime.now(UTC)
    await db.execute(
        update(Node).where(Node.node_id == node_id).values(online=True, last_seen_at=now)
    )
    await db.commit()


async def mark_offline(db: AsyncSession, *, client_id: str | None, username: str | None) -> None:
    node_id = username or client_id
    if not node_id:
        return
    now = datetime.now(UTC)
    await db.execute(
        update(Node).where(Node.node_id == node_id).values(online=False, last_seen_at=now)
    )
    await db.commit()
