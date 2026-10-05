"""catalog admin: enforce one active route binding per model alias

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-05

The runtime resolver rejects multiple active routes for one alias as
``AmbiguousRoute`` (no route-selection policy exists). This migration adds a
PostgreSQL partial unique index so the database itself forbids that state:

    CREATE UNIQUE INDEX uq_route_bindings_one_active_per_alias
        ON route_bindings (model_alias_id)
        WHERE is_active = true;

Inactive alternate routes remain permitted (deactivate-then-activate swaps are
supported). If an existing database already contains multiple active routes for
one alias, the migration fails with a clear diagnostic naming the offending
alias IDs rather than silently choosing a winner.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_route_bindings_one_active_per_alias"


def upgrade() -> None:
    conn = op.get_bind()
    dupes = conn.execute(
        sa.text(
            """
            SELECT model_alias_id
            FROM route_bindings
            WHERE is_active = true
            GROUP BY model_alias_id
            HAVING count(*) > 1
            """
        )
    ).fetchall()
    if dupes:
        ids = ", ".join(str(row[0]) for row in dupes)
        raise RuntimeError(
            "cannot create one-active-route-per-alias index: the following "
            f"model aliases already have multiple active route bindings: {ids}. "
            "Deactivate all but one active route per alias before upgrading."
        )

    op.create_index(
        INDEX_NAME,
        "route_bindings",
        ["model_alias_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="route_bindings")
