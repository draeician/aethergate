"""identity phase 2 hardening: role/scope coherence constraints

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05

Enforces the RBAC role/scope shape at the database layer so application
invariants cannot be bypassed by direct writes:

- ``system_admin`` must be deployment-scoped with no project resource ID;
- ``project_admin``/``project_viewer`` must be project-scoped (with a project
  resource ID, already required by ``ck_role_assignments_project_scope_requires_resource``).

Valid role/scope enums and active-equivalent uniqueness were already enforced by
0010 and are left untouched.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_role_assignments_system_admin_deployment",
        "role_assignments",
        sa.text(
            "role <> 'system_admin' OR "
            "(resource_scope_type = 'deployment' AND resource_id = '')"
        ),
    )
    op.create_check_constraint(
        "ck_role_assignments_project_role_project_scope",
        "role_assignments",
        sa.text("role = 'system_admin' OR resource_scope_type = 'project'"),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_role_assignments_project_role_project_scope",
        "role_assignments",
        type_="check",
    )
    op.drop_constraint(
        "ck_role_assignments_system_admin_deployment",
        "role_assignments",
        type_="check",
    )
