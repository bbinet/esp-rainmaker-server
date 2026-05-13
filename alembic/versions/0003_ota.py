"""Phase 6 — OTA: ota_images, ota_jobs, ota_job_nodes.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ota_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("fw_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_md5", sa.String(64), nullable=True),
        sa.Column("file_sha256", sa.String(128), nullable=True),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="staged"),
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
    op.create_index("ix_ota_images_fw_version", "ota_images", ["fw_version"])

    op.create_table(
        "ota_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "image_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ota_images.id", ondelete="CASCADE", name="fk_ota_jobs_image_id_ota_images"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "rollout_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_ota_jobs_status", "ota_jobs", ["status"])

    op.create_table(
        "ota_job_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ota_jobs.id", ondelete="CASCADE", name="fk_ota_job_nodes_job_id_ota_jobs"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE", name="fk_ota_job_nodes_node_id_nodes"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("additional_info", sa.String(512), nullable=True),
        sa.Column("last_update", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("job_id", "node_id", name="uq_ota_job_node"),
    )
    op.create_index("ix_ota_job_nodes_node_id", "ota_job_nodes", ["node_id"])
    op.create_index("ix_ota_job_nodes_status", "ota_job_nodes", ["status"])


def downgrade() -> None:
    op.drop_table("ota_job_nodes")
    op.drop_table("ota_jobs")
    op.drop_table("ota_images")
