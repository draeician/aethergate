"""shared provider-account request/token quotas

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    # QuotaGroup -> ProviderAccount ownership (a group belongs to exactly one
    # account). The quota_groups table is empty in every deployment (no admin
    # CRUD path yet), so the NOT NULL column is safe.
    op.add_column(
        "quota_groups",
        sa.Column(
            "provider_account_id",
            sa.String(64),
            sa.ForeignKey("provider_accounts.id"),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index("ix_quota_groups_provider_account_id", "quota_groups", ["provider_account_id"])
    op.alter_column("quota_groups", "provider_account_id", server_default=None)
    op.add_column(
        "quota_groups",
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "quota_limits",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "quota_group_id",
            sa.String(64),
            sa.ForeignKey("quota_groups.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("limit_units", sa.Integer, nullable=False),
        sa.Column("window_seconds", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("name", sa.String(255), nullable=True),
        sa.CheckConstraint("limit_units >= 1", name="ck_quota_limits_units_positive"),
        sa.CheckConstraint("window_seconds >= 1", name="ck_quota_limits_window_positive"),
        *_timestamps(),
    )

    op.create_table(
        "quota_windows",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "quota_limit_id",
            sa.String(64),
            sa.ForeignKey("quota_limits.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("committed_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reserved_units", sa.Integer, nullable=False, server_default="0"),
        *_timestamps(),
    )
    op.create_unique_constraint(
        "uq_quota_windows_limit_start", "quota_windows", ["quota_limit_id", "window_start"]
    )

    op.create_table(
        "quota_reservations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(64),
            sa.ForeignKey("inference_requests.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "quota_limit_id",
            sa.String(64),
            sa.ForeignKey("quota_limits.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("reserved_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("committed_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("state", sa.String(32), nullable=False, server_default="reserved"),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )

    op.add_column(
        "route_bindings",
        sa.Column("default_output_tokens", sa.Integer, nullable=True),
    )

    op.add_column(
        "inference_requests",
        sa.Column("wait_reason", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inference_requests", "wait_reason")
    op.drop_column("route_bindings", "default_output_tokens")
    op.drop_table("quota_reservations")
    op.drop_constraint("uq_quota_windows_limit_start", "quota_windows", type_="unique")
    op.drop_table("quota_windows")
    op.drop_table("quota_limits")
    op.drop_column("quota_groups", "cooldown_until")
    op.drop_index("ix_quota_groups_provider_account_id", table_name="quota_groups")
    op.drop_column("quota_groups", "provider_account_id")
