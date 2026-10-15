"""add per-route thinking (reasoning) policy

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-15

Adds an administrative, per-route reasoning ("thinking") policy to
``route_bindings``. The column defaults to ``inherit`` for all existing routes,
preserving the previous behavior (no explicit thinking directive is sent
upstream). A CHECK constraint restricts the value to the supported policy set.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

CHECK_NAME = "ck_route_bindings_thinking_policy"


def upgrade() -> None:
    op.add_column(
        "route_bindings",
        sa.Column(
            "thinking_policy",
            sa.String(16),
            nullable=False,
            server_default="inherit",
        ),
    )
    op.create_check_constraint(
        CHECK_NAME,
        "route_bindings",
        "thinking_policy IN ('inherit', 'off', 'on', 'low', 'medium', 'high')",
    )


def downgrade() -> None:
    op.drop_constraint(CHECK_NAME, "route_bindings", type_="check")
    op.drop_column("route_bindings", "thinking_policy")
