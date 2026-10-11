"""identity phase 2: admin RBAC, bootstrap state, audit events

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-05

Adds the control-plane identity primitives:

- ``role_assignments`` — durable role grants (system_admin/project_admin/
  project_viewer) with deployment/project resource scope, revocation, and a
  partial unique index preventing duplicate active equivalent assignments.
- ``bootstrap_state`` — singleton DB-authoritative one-use bootstrap completion
  state (safe metadata only; no secret).
- ``audit_events`` — immutable administrative audit events (safe metadata only).

No raw key, hash, bootstrap token, or provider secret is invented or stored.
Existing inference credentials are untouched.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_assignments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(64),
            sa.ForeignKey("principals.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("resource_scope_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('system_admin', 'project_admin', 'project_viewer')",
            name="ck_role_assignments_role",
        ),
        sa.CheckConstraint(
            "resource_scope_type IN ('deployment', 'project')",
            name="ck_role_assignments_scope_type",
        ),
        sa.CheckConstraint(
            "resource_scope_type <> 'project' OR resource_id <> ''",
            name="ck_role_assignments_project_scope_requires_resource",
        ),
    )
    op.create_index(
        "uq_role_assignments_active_equivalent",
        "role_assignments",
        ["principal_id", "role", "resource_scope_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND revoked_at IS NULL"),
    )

    op.create_table(
        "bootstrap_state",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initial_project_id", sa.String(64), nullable=True),
        sa.Column("initial_admin_principal_id", sa.String(64), nullable=True),
        sa.Column("initial_admin_credential_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor_principal_id", sa.String(64), nullable=True, index=True),
        sa.Column("project_id", sa.String(64), nullable=True, index=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("bootstrap_state")
    op.drop_index("uq_role_assignments_active_equivalent", table_name="role_assignments")
    op.drop_table("role_assignments")
