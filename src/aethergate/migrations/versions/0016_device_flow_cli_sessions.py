"""identity phase 4: device authorization transactions and human CLI sessions

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-06

Adds the human CLI identity primitives for the OAuth device flow:

- ``device_authorizations`` — one-time RFC 8628 device-authorization
  transactions. Only the one-way SHA-256 ``device_code_hash`` is persisted; the
  raw ``device_code`` is a bearer secret returned once at ``device/start`` and
  never stored. ``user_code``/``verification_uri`` are non-secret display
  values.
- ``cli_sessions`` — durable human CLI sessions. Only the one-way SHA-256
  verifier of the raw ``ags_...`` token is persisted; the raw token is returned
  exactly once at device-flow success.

No raw device_code, provider access/refresh/ID token, CLI session token, or
client secret is invented or persisted by this migration. Existing
service-account credentials, browser sessions, and role assignments are
unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_authorizations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("device_code_hash", sa.String(64), nullable=False),
        sa.Column("user_code", sa.String(32), nullable=False),
        sa.Column("verification_uri", sa.Text(), nullable=False),
        sa.Column("verification_uri_complete", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "poll_interval_seconds", sa.Integer(), nullable=False, server_default="5"
        ),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_device_authorizations_expiry_order"
        ),
        sa.CheckConstraint(
            "poll_interval_seconds >= 1",
            name="ck_device_authorizations_poll_interval_positive",
        ),
    )
    op.create_index(
        "uq_device_authorizations_code_hash",
        "device_authorizations",
        ["device_code_hash"],
        unique=True,
    )

    op.create_table(
        "cli_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(64),
            sa.ForeignKey("principals.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_cli_sessions_expiry_order"
        ),
    )
    op.create_index(
        "uq_cli_sessions_token_hash",
        "cli_sessions",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_cli_sessions_token_hash", table_name="cli_sessions")
    op.drop_table("cli_sessions")
    op.drop_index(
        "uq_device_authorizations_code_hash", table_name="device_authorizations"
    )
    op.drop_table("device_authorizations")
