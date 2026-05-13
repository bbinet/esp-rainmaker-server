"""Phase 2 — claiming: device_provisioning and claim_challenges.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_provisioning",
        sa.Column("mac_addr", sa.String(32), primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("hmac_key", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "claim_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("auth_id", sa.String(64), nullable=True),
        sa.Column("mac_addr", sa.String(32), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("challenge", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_claim_challenges_auth_id", "claim_challenges", ["auth_id"], unique=True)
    op.create_index("ix_claim_challenges_mac_addr", "claim_challenges", ["mac_addr"])
    op.create_index("ix_claim_challenges_node_id", "claim_challenges", ["node_id"])
    op.create_index("ix_claim_challenges_user_id", "claim_challenges", ["user_id"])


def downgrade() -> None:
    op.drop_table("claim_challenges")
    op.drop_table("device_provisioning")
