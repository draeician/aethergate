"""add endpoint operational state (active/paused/draining)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-05

Adds a durable scheduler/operator state to endpoints, distinct from catalog
``is_active`` (which remains configuration/lifecycle state). The new column is
``operational_state`` with default ``active`` for all existing endpoints, and a
CHECK constraint restricting values to ``active``/``paused``/``draining``.

Pause/drain is a dispatch gate, not a deactivation: it never touches
``is_active``, and existing endpoint IDs/configuration are preserved.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

CHECK_NAME = "ck_endpoints_operational_state"


def upgrade() -> None:
    op.add_column(
        "endpoints",
        sa.Column(
            "operational_state",
            sa.String(32),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_check_constraint(
        CHECK_NAME,
        "endpoints",
        "operational_state IN ('active', 'paused', 'draining')",
    )


def downgrade() -> None:
    op.drop_constraint(CHECK_NAME, "endpoints", type_="check")
    op.drop_column("endpoints", "operational_state")
