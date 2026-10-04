"""scheduler phase 1 correctness hardening

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Endpoint physical concurrency must be constrained positive at the database
    # layer as well as the domain/DTO layer.
    op.create_check_constraint(
        "ck_endpoints_max_concurrency_positive",
        "endpoints",
        "max_concurrency >= 1",
    )

    # Operator reconciliation of ambiguous (outcome_unknown) executions.
    op.add_column(
        "inference_requests",
        sa.Column("reconciled_state", sa.String(32), nullable=True),
    )
    op.add_column(
        "inference_requests",
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inference_requests",
        sa.Column("reconciled_by", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inference_requests", "reconciled_by")
    op.drop_column("inference_requests", "reconciled_at")
    op.drop_column("inference_requests", "reconciled_state")
    op.drop_constraint(
        "ck_endpoints_max_concurrency_positive", "endpoints", type_="check"
    )
