"""identity phase 3: human OIDC external identities and browser sessions

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-05

Adds the human-identity primitives for OIDC authorization-code login:

- ``external_identities`` — durable link from an OIDC provider identity
  ``(issuer, subject)`` to a Principal. Unique on ``(issuer, subject)``; subject
  is opaque/case-sensitive; email is a non-authoritative display claim only.
- ``browser_sessions`` — server-managed sessions storing only one-way verifiers
  (SHA-256 of the raw cookie and raw CSRF token), idle/absolute expiry, and
  revocation. Raw cookie/CSRF values are never persisted.
- ``oidc_login_states`` — one-time authorization-code login transactions
  (state/nonce/PKCE S256 challenge + verifier, short expiry, single use).

No raw ID/access/refresh token, state, nonce, PKCE verifier, session cookie, CSRF
token, or client secret is invented or persisted by this migration. Existing
service-account credentials and role assignments are unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(64),
            sa.ForeignKey("principals.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("issuer", "subject", name="uq_external_identities_issuer_subject"),
    )

    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(64),
            sa.ForeignKey("principals.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "idle_expires_at < absolute_expires_at",
            name="ck_browser_sessions_expiry_order",
        ),
    )
    op.create_index(
        "uq_browser_sessions_session_hash",
        "browser_sessions",
        ["session_hash"],
        unique=True,
    )

    op.create_table(
        "oidc_login_states",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        "uq_oidc_login_states_state",
        "oidc_login_states",
        ["state"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_oidc_login_states_state", table_name="oidc_login_states")
    op.drop_table("oidc_login_states")
    op.drop_index("uq_browser_sessions_session_hash", table_name="browser_sessions")
    op.drop_table("browser_sessions")
    op.drop_table("external_identities")
