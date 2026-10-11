"""add upstream_model to route_bindings

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "route_bindings",
        sa.Column("upstream_model", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("route_bindings", "upstream_model")
