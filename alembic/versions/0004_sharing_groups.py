"""Phase 7 — sharing & groups.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_sharing",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE", name="fk_node_sharing_node_id_nodes"),
            nullable=False,
        ),
        sa.Column(
            "from_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_node_sharing_from_user_id_users"),
            nullable=False,
        ),
        sa.Column(
            "to_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_node_sharing_to_user_id_users"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False, server_default="secondary"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("node_id", "to_user_id", name="uq_node_sharing_node_user"),
    )
    op.create_index("ix_node_sharing_to_user", "node_sharing", ["to_user_id"])

    op.create_table(
        "node_sharing_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE", name="fk_node_sharing_req_node_id_nodes"),
            nullable=False,
        ),
        sa.Column(
            "from_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_node_sharing_req_from_user_id"),
            nullable=False,
        ),
        sa.Column("to_user_name", sa.String(254), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_node_sharing_req_to_user_name", "node_sharing_requests", ["to_user_name"])
    op.create_index("ix_node_sharing_req_status", "node_sharing_requests", ["status"])

    op.create_table(
        "node_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_node_groups_owner_id_users"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("node_groups.id", ondelete="CASCADE", name="fk_node_groups_parent_id"),
            nullable=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(64), nullable=True),
        sa.Column("mutually_exclusive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_node_groups_owner_id", "node_groups", ["owner_id"])
    op.create_index("ix_node_groups_parent_id", "node_groups", ["parent_id"])

    op.create_table(
        "node_group_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("node_groups.id", ondelete="CASCADE", name="fk_node_group_nodes_group_id"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE", name="fk_node_group_nodes_node_id"),
            nullable=False,
        ),
        sa.UniqueConstraint("group_id", "node_id", name="uq_node_group_node"),
    )


def downgrade() -> None:
    op.drop_table("node_group_nodes")
    op.drop_table("node_groups")
    op.drop_table("node_sharing_requests")
    op.drop_table("node_sharing")
