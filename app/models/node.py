from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user_node import UserNodeMapping


class Node(Base, TimestampMixin):
    __tablename__ = "nodes"

    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fw_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mac_addr: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    registration_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    attributes: Mapped[list[NodeAttribute]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )
    config: Mapped[NodeConfig | None] = relationship(
        back_populates="node", cascade="all, delete-orphan", uselist=False
    )
    params_shadow: Mapped[NodeParamsShadow | None] = relationship(
        back_populates="node", cascade="all, delete-orphan", uselist=False
    )
    mappings: Mapped[list[UserNodeMapping]] = relationship(back_populates="node")


class NodeAttribute(Base):
    __tablename__ = "node_attributes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)

    node: Mapped[Node] = relationship(back_populates="attributes")

    __table_args__ = (UniqueConstraint("node_id", "name", name="uq_node_attribute"),)


class NodeConfig(Base, TimestampMixin):
    __tablename__ = "node_configs"

    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), primary_key=True
    )
    config_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    node: Mapped[Node] = relationship(back_populates="config")


class NodeParamsShadow(Base, TimestampMixin):
    __tablename__ = "node_params_shadow"

    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), primary_key=True
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    node: Mapped[Node] = relationship(back_populates="params_shadow")


class NodeCertificate(Base, TimestampMixin):
    __tablename__ = "node_certificates"

    serial: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cert_pem: Mapped[str] = mapped_column(Text, nullable=False)
    cn: Mapped[str] = mapped_column(String(128), nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policies: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    __table_args__ = (Index("ix_node_cert_active", "node_id", "revoked"),)
