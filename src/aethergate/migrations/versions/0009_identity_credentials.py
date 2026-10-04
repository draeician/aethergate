"""identity phase 1: scoped inference API credentials

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-04

Refines ``api_credentials`` from a ``secret_ref_id``-based placeholder into a
one-way-verifiable client credential:

- ``key_prefix`` (non-secret display prefix) and ``key_hash`` (SHA-256 verifier,
  unique index) replace ``secret_ref_id``;
- ``audience`` and ``scopes`` (JSON) carry the credential's surface/permissions;
- ``expires_at``, ``revoked_at``, ``last_used_at`` carry lifecycle state.

No raw key is created or stored by this migration. Existing rows (the earlier
development credential) are backfilled with the inference audience and the
``inference:invoke`` scope only; no recoverable raw key is invented for them,
so they remain non-authenticatable and must be replaced by newly generated
credentials for real authentication.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_credentials", sa.Column("key_prefix", sa.String(64), nullable=True)
    )
    op.add_column(
        "api_credentials", sa.Column("key_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "api_credentials",
        sa.Column(
            "audience", sa.String(32), nullable=False, server_default="inference"
        ),
    )
    op.add_column(
        "api_credentials",
        sa.Column(
            "scopes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[\"inference:invoke\"]'::json"),
        ),
    )
    op.add_column(
        "api_credentials",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_credentials",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_credentials",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "uq_api_credentials_key_hash", "api_credentials", ["key_hash"], unique=True
    )
    op.create_check_constraint(
        "ck_api_credentials_audience",
        "api_credentials",
        "audience IN ('inference', 'admin')",
    )

    op.drop_constraint(
        "api_credentials_secret_ref_id_fkey", "api_credentials", type_="foreignkey"
    )
    op.drop_column("api_credentials", "secret_ref_id")


def downgrade() -> None:
    op.add_column(
        "api_credentials",
        sa.Column(
            "secret_ref_id",
            sa.String(64),
            sa.ForeignKey("secret_refs.id"),
            nullable=True,
        ),
    )
    op.drop_constraint(
        "ck_api_credentials_audience", "api_credentials", type_="check"
    )
    op.drop_index("uq_api_credentials_key_hash", table_name="api_credentials")
    op.drop_column("api_credentials", "last_used_at")
    op.drop_column("api_credentials", "revoked_at")
    op.drop_column("api_credentials", "expires_at")
    op.drop_column("api_credentials", "scopes")
    op.drop_column("api_credentials", "audience")
    op.drop_column("api_credentials", "key_hash")
    op.drop_column("api_credentials", "key_prefix")
