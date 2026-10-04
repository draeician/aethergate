"""accounting invariants: one enabled price policy per route, billing-unit shape

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04

Adds:
- a partial unique index enforcing at most one enabled ``PricePolicy`` per route;
- billing-unit-specific CHECK constraints (request vs token price shape);
- relaxes ``budget_reservations.price_snapshot_id`` to nullable so a pre-dispatch
  reservation can be released without retaining an orphan price snapshot.

Existing rows that violate the new invariants are detected and reported before
any constraint is added; the migration refuses to guess at a repair.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _fail(offenders: str) -> None:
    raise RuntimeError(
        "migration 0008 cannot proceed: existing price_policies violate the new "
        "accounting invariants and cannot be safely repaired without guessing.\n"
        f"{offenders}"
    )


def upgrade() -> None:
    bind = op.get_bind()

    shape_violations = bind.execute(
        sa.text(
            """
            SELECT id, billing_unit, request_price, input_price, output_price
            FROM price_policies
            WHERE (
                billing_unit = 'request' AND (
                    request_price IS NULL
                    OR input_price IS NOT NULL
                    OR output_price IS NOT NULL
                )
            ) OR (
                billing_unit = 'token' AND (
                    input_price IS NULL
                    OR output_price IS NULL
                    OR request_price IS NOT NULL
                )
            )
            """
        )
    ).fetchall()
    if shape_violations:
        _fail(
            "billing-unit price-shape violations: "
            + ", ".join(str(row[0]) for row in shape_violations)
        )

    duplicate_routes = bind.execute(
        sa.text(
            """
            SELECT route_binding_id, count(*) AS n
            FROM price_policies
            WHERE enabled
            GROUP BY route_binding_id
            HAVING count(*) > 1
            """
        )
    ).fetchall()
    if duplicate_routes:
        _fail(
            "multiple enabled price policies per route: "
            + ", ".join(f"{row[0]} ({row[1]})" for row in duplicate_routes)
        )

    op.create_check_constraint(
        "ck_price_policies_request_shape",
        "price_policies",
        "billing_unit <> 'request' OR "
        "(request_price IS NOT NULL AND input_price IS NULL AND output_price IS NULL)",
    )
    op.create_check_constraint(
        "ck_price_policies_token_shape",
        "price_policies",
        "billing_unit <> 'token' OR "
        "(request_price IS NULL AND input_price IS NOT NULL AND output_price IS NOT NULL)",
    )
    op.create_index(
        "uq_price_policies_one_enabled_per_route",
        "price_policies",
        ["route_binding_id"],
        unique=True,
        postgresql_where=sa.text("enabled"),
    )
    op.alter_column(
        "budget_reservations",
        "price_snapshot_id",
        existing_type=sa.String(64),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "budget_reservations",
        "price_snapshot_id",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.drop_index("uq_price_policies_one_enabled_per_route", table_name="price_policies")
    op.drop_constraint("ck_price_policies_token_shape", "price_policies", type_="check")
    op.drop_constraint("ck_price_policies_request_shape", "price_policies", type_="check")
