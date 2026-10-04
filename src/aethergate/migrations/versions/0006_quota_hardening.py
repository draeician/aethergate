"""harden shared-quota schema invariants and add wait/scope metadata

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _reject_invalid_rows(bind, *, table: str, where: str, message: str) -> None:
    """Raise a clear error if ``table`` contains any row matching ``where``.

    New constraints must never silently accept historical rows that would violate
    them; the migration rejects such rows explicitly instead.
    """
    count = bind.execute(
        text(f"SELECT count(*) FROM {table} WHERE {where}")
    ).scalar()
    if count:
        raise RuntimeError(
            f"migration 0006: {table} has {count} row(s) that violate "
            f"{message}; repair or remove them before upgrading"
        )


def upgrade() -> None:
    bind = op.get_bind()

    # Explicitly reject any historical rows that the new invariants would forbid,
    # rather than silently accepting (or cryptically failing on) invalid state.
    _reject_invalid_rows(
        bind,
        table="quota_limits",
        where="metric NOT IN ('requests', 'tokens')",
        message="quota_limits.metric must be 'requests' or 'tokens'",
    )
    _reject_invalid_rows(
        bind,
        table="quota_reservations",
        where="metric NOT IN ('requests', 'tokens')",
        message="quota_reservations.metric must be 'requests' or 'tokens'",
    )
    _reject_invalid_rows(
        bind,
        table="quota_reservations",
        where="state NOT IN ('reserved', 'committed', 'released')",
        message="quota_reservations.state must be reserved/committed/released",
    )
    _reject_invalid_rows(
        bind,
        table="quota_windows",
        where="committed_units < 0 OR reserved_units < 0",
        message="quota_windows committed/reserved units must be nonnegative",
    )
    _reject_invalid_rows(
        bind,
        table="quota_reservations",
        where="reserved_units < 0 OR committed_units < 0",
        message="quota_reservations reserved/committed units must be nonnegative",
    )
    _reject_invalid_rows(
        bind,
        table="route_bindings",
        where="default_output_tokens IS NOT NULL AND default_output_tokens < 1",
        message="route_bindings.default_output_tokens must be positive when set",
    )

    # Effective scheduling scope + wait metadata (non-content) on queued requests.
    op.add_column(
        "inference_requests",
        sa.Column(
            "quota_group_id",
            sa.String(64),
            sa.ForeignKey("quota_groups.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_inference_requests_quota_group_id", "inference_requests", ["quota_group_id"]
    )
    op.add_column(
        "inference_requests",
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inference_requests", sa.Column("wait_limit_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "inference_requests", sa.Column("wait_limit_metric", sa.String(32), nullable=True)
    )

    # Schema invariants.
    op.create_check_constraint(
        "ck_quota_limits_metric", "quota_limits", "metric IN ('requests', 'tokens')"
    )
    op.create_check_constraint(
        "ck_route_bindings_default_output_positive",
        "route_bindings",
        "default_output_tokens IS NULL OR default_output_tokens >= 1",
    )
    op.create_check_constraint(
        "ck_quota_windows_committed_nonneg", "quota_windows", "committed_units >= 0"
    )
    op.create_check_constraint(
        "ck_quota_windows_reserved_nonneg", "quota_windows", "reserved_units >= 0"
    )
    op.create_check_constraint(
        "ck_quota_reservations_reserved_nonneg",
        "quota_reservations",
        "reserved_units >= 0",
    )
    op.create_check_constraint(
        "ck_quota_reservations_committed_nonneg",
        "quota_reservations",
        "committed_units >= 0",
    )
    op.create_check_constraint(
        "ck_quota_reservations_metric",
        "quota_reservations",
        "metric IN ('requests', 'tokens')",
    )
    op.create_check_constraint(
        "ck_quota_reservations_state",
        "quota_reservations",
        "state IN ('reserved', 'committed', 'released')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_quota_reservations_state", "quota_reservations", type_="check"
    )
    op.drop_constraint(
        "ck_quota_reservations_metric", "quota_reservations", type_="check"
    )
    op.drop_constraint(
        "ck_quota_reservations_committed_nonneg", "quota_reservations", type_="check"
    )
    op.drop_constraint(
        "ck_quota_reservations_reserved_nonneg", "quota_reservations", type_="check"
    )
    op.drop_constraint(
        "ck_quota_windows_reserved_nonneg", "quota_windows", type_="check"
    )
    op.drop_constraint(
        "ck_quota_windows_committed_nonneg", "quota_windows", type_="check"
    )
    op.drop_constraint(
        "ck_route_bindings_default_output_positive", "route_bindings", type_="check"
    )
    op.drop_constraint("ck_quota_limits_metric", "quota_limits", type_="check")

    op.drop_column("inference_requests", "wait_limit_metric")
    op.drop_column("inference_requests", "wait_limit_id")
    op.drop_column("inference_requests", "next_eligible_at")
    op.drop_index("ix_inference_requests_quota_group_id", table_name="inference_requests")
    op.drop_column("inference_requests", "quota_group_id")
