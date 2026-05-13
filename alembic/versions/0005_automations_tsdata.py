"""Phase 8 — automations + TimescaleDB hypertable for tsdata.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_automations_user_id_users"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("event_operator", sa.String(8), nullable=False, server_default="and"),
        sa.Column(
            "events", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
        sa.Column(
            "actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
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
    op.create_index("ix_automations_user_id", "automations", ["user_id"])
    op.create_index("ix_automations_enabled", "automations", ["enabled"])

    op.create_table(
        "tsdata",
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("device_name", sa.String(64), nullable=False),
        sa.Column("param_name", sa.String(64), nullable=False),
        sa.Column("data_type", sa.String(16), nullable=False),
        sa.Column("value_int", sa.BigInteger(), nullable=True),
        sa.Column("value_float", sa.Float(), nullable=True),
        sa.Column("value_bool", sa.Boolean(), nullable=True),
        sa.Column("value_str", sa.Text(), nullable=True),
    )
    op.create_index("ix_tsdata_node_id_ts", "tsdata", ["node_id", "ts"])
    op.create_index(
        "ix_tsdata_node_param_ts", "tsdata", ["node_id", "device_name", "param_name", "ts"]
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute(
        "SELECT create_hypertable('tsdata', 'ts', "
        "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.drop_table("tsdata")
    op.drop_table("automations")
