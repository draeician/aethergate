"""identity phase 3 hardening: bind OIDC login transaction to the browser

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-05

Adds a one-way transaction-cookie verifier to ``oidc_login_states`` so the login
transaction is cryptographically bound to the initiating browser (login-CSRF /
login-state-injection defense). Only the SHA-256 ``txn_cookie_hash`` of the raw
short-lived transaction cookie is persisted; the raw cookie value is never
stored.

No raw token, cookie, nonce, PKCE material, or client secret is invented or
persisted by this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oidc_login_states",
        sa.Column("txn_cookie_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.alter_column("oidc_login_states", "txn_cookie_hash", server_default=None)


def downgrade() -> None:
    op.drop_column("oidc_login_states", "txn_cookie_hash")
