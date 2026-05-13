"""mTLS-based authentication for Node-facing endpoints.

Ingress enforces the TLS verify and propagates the verified CN as
``X-SSL-Client-CN``. We trust that header *only* on the Node Ingress
host — never on the user-facing one. NetworkPolicies ensure no other
path reaches us, and in tests we set the header directly.
"""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.db import get_db
from app.core.errors import unauthorized
from app.models.node import Node, NodeCertificate


async def get_node_from_mtls(
    x_ssl_client_cn: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Node:
    if not x_ssl_client_cn:
        raise unauthorized("Client certificate required")

    cert = (
        (
            await db.execute(
                select(NodeCertificate)
                .where(
                    NodeCertificate.cn == x_ssl_client_cn,
                    NodeCertificate.revoked.is_(False),
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if cert is None:
        raise unauthorized("Unknown or revoked client certificate")

    node = (await db.execute(select(Node).where(Node.node_id == cert.node_id))).scalar_one_or_none()
    if node is None:
        raise unauthorized("Node not registered")
    return node
