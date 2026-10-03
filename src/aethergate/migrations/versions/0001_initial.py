"""initial v2 persistence schema

Revision ID: 0001
Revises:
Create Date: 2026-10-03

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
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
    op.create_table(
        "projects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "principals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "secret_refs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        *_timestamps(),
    )

    op.create_table(
        "api_credentials",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "principal_id",
            sa.String(64),
            sa.ForeignKey("principals.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "secret_ref_id",
            sa.String(64),
            sa.ForeignKey("secret_refs.id"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "providers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("capabilities", sa.JSON, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "provider_accounts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "provider_id",
            sa.String(64),
            sa.ForeignKey("providers.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=True),
        sa.Column(
            "secret_ref_id",
            sa.String(64),
            sa.ForeignKey("secret_refs.id"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "endpoints",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "provider_account_id",
            sa.String(64),
            sa.ForeignKey("provider_accounts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("base_destination", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "quota_groups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "model_aliases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("capabilities", sa.JSON, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "route_bindings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "model_alias_id",
            sa.String(64),
            sa.ForeignKey("model_aliases.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "endpoint_id",
            sa.String(64),
            sa.ForeignKey("endpoints.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "provider_account_id",
            sa.String(64),
            sa.ForeignKey("provider_accounts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "quota_group_id",
            sa.String(64),
            sa.ForeignKey("quota_groups.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("route_bindings")
    op.drop_table("model_aliases")
    op.drop_table("quota_groups")
    op.drop_table("endpoints")
    op.drop_table("provider_accounts")
    op.drop_table("providers")
    op.drop_table("api_credentials")
    op.drop_table("secret_refs")
    op.drop_table("principals")
    op.drop_table("projects")
