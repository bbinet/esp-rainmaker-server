from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.node import Node
    from app.models.user import User


class UserNodeMapping(Base, TimestampMixin):
    """A user holds a (primary | secondary) role over a node.

    `primary` is the original mapping created via the secret_key /
    challenge-response flow. Sharing produces secondary mappings.
    """

    __tablename__ = "user_node_mappings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), default="primary", nullable=False)
    primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    user: Mapped[User] = relationship(back_populates="mappings")
    node: Mapped[Node] = relationship(back_populates="mappings")

    __table_args__ = (
        UniqueConstraint("user_id", "node_id", name="uq_user_node"),
        Index("ix_user_node_node", "node_id"),
    )


class MappingChallenge(Base, TimestampMixin):
    """Pending user->node mapping awaiting confirmation from device.

    Two shapes are supported:

    * **Secret-key (legacy)**: phone PUTs `/user/nodes/mapping` with
      `{node_id, secret_key}`; backend stores it here; device publishes to
      `node/<id>/user/mapping` with the same `secret_key`; ingestor
      matches → mapping is created.
    * **Challenge-response**: `/user/nodes/mapping/initiate` returns
      `request_id` + `challenge`; phone hands it to the device via
      protocomm; device replies; `/user/nodes/mapping/verify` finalises.
    """

    __tablename__ = "mapping_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    secret_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    challenge: Mapped[str | None] = mapped_column(String(256), nullable=True)
    challenge_response: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    operation: Mapped[str] = mapped_column(String(16), default="add", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
