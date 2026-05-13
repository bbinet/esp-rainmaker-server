"""Initial schema: users, refresh_tokens, nodes, node_certificates,
node_configs, node_attributes, node_params_shadow, user_node_mappings,
mapping_challenges.

Revision ID: 0001
Revises:
Create Date: 2026-05-13

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"pgcrypto\"")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_name", sa.String(254), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("phone_number", sa.String(32), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="unconfirmed"),
        sa.Column("is_super_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("custom_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("confirm_code", sa.String(16), nullable=True),
        sa.Column("confirm_code_exp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_code", sa.String(16), nullable=True),
        sa.Column("reset_code_exp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("login_otp", sa.String(16), nullable=True),
        sa.Column("login_otp_exp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_name", name="uq_users_user_name_ci"),
    )
    op.create_index("ix_users_user_name", "users", ["user_name"], unique=True)
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_email_lower", "users", ["email"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_refresh_tokens_user_id_users"),
            nullable=False,
        ),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)

    op.create_table(
        "nodes",
        sa.Column("node_id", sa.String(64), primary_key=True),
        sa.Column("node_type", sa.String(64), nullable=True),
        sa.Column("fw_version", sa.String(64), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("project_name", sa.String(128), nullable=True),
        sa.Column("platform", sa.String(32), nullable=True),
        sa.Column("mac_addr", sa.String(32), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("registration_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_nodes_mac_addr", "nodes", ["mac_addr"])

    op.create_table(
        "node_certificates",
        sa.Column("serial", sa.String(64), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("cert_pem", sa.Text(), nullable=False),
        sa.Column("cn", sa.String(128), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policies", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_node_certificates_node_id", "node_certificates", ["node_id"])
    op.create_index("ix_node_cert_active", "node_certificates", ["node_id", "revoked"])

    op.create_table(
        "node_configs",
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE", name="fk_node_configs_node_id_nodes"),
            primary_key=True,
        ),
        sa.Column("config_version", sa.String(32), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "node_params_shadow",
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE", name="fk_node_params_shadow_node_id_nodes"),
            primary_key=True,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "node_attributes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE", name="fk_node_attributes_node_id_nodes"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.UniqueConstraint("node_id", "name", name="uq_node_attribute"),
    )

    op.create_table(
        "user_node_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_user_node_mappings_user_id_users"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE", name="fk_user_node_mappings_node_id_nodes"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False, server_default="primary"),
        sa.Column("primary", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "node_id", name="uq_user_node"),
    )
    op.create_index("ix_user_node_node", "user_node_mappings", ["node_id"])

    op.create_table(
        "mapping_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_mapping_challenges_user_id_users"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(64), nullable=True),
        sa.Column("secret_key", sa.String(128), nullable=True),
        sa.Column("challenge", sa.String(256), nullable=True),
        sa.Column("challenge_response", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("operation", sa.String(16), nullable=False, server_default="add"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mapping_challenges_request_id", "mapping_challenges", ["request_id"], unique=True)
    op.create_index("ix_mapping_challenges_node_id", "mapping_challenges", ["node_id"])
    op.create_index("ix_mapping_challenges_secret_key", "mapping_challenges", ["secret_key"])


def downgrade() -> None:
    op.drop_table("mapping_challenges")
    op.drop_table("user_node_mappings")
    op.drop_table("node_attributes")
    op.drop_table("node_params_shadow")
    op.drop_table("node_configs")
    op.drop_table("node_certificates")
    op.drop_table("nodes")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
