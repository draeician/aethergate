"""accounting foundation: pricing, usage, and optional project budgets

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(24, 12)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    # Mutable route pricing configuration (never the historical record).
    op.create_table(
        "price_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "route_binding_id",
            sa.String(64),
            sa.ForeignKey("route_bindings.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("billing_unit", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("unit_scale", sa.Integer, nullable=False, server_default="1"),
        sa.Column("request_price", MONEY, nullable=True),
        sa.Column("input_price", MONEY, nullable=True),
        sa.Column("output_price", MONEY, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("name", sa.String(255), nullable=True),
        sa.CheckConstraint(
            "billing_unit IN ('request', 'token')", name="ck_price_policies_billing_unit"
        ),
        sa.CheckConstraint("unit_scale >= 1", name="ck_price_policies_unit_scale_positive"),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_price_policies_currency_format"
        ),
        sa.CheckConstraint(
            "request_price IS NULL OR request_price >= 0",
            name="ck_price_policies_request_price_nonneg",
        ),
        sa.CheckConstraint(
            "input_price IS NULL OR input_price >= 0",
            name="ck_price_policies_input_price_nonneg",
        ),
        sa.CheckConstraint(
            "output_price IS NULL OR output_price >= 0",
            name="ck_price_policies_output_price_nonneg",
        ),
        *_timestamps(),
    )

    # Immutable price snapshots captured at the dispatch decision.
    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "source_price_policy_id",
            sa.String(64),
            sa.ForeignKey("price_policies.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "route_binding_id",
            sa.String(64),
            sa.ForeignKey("route_bindings.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "provider_account_id",
            sa.String(64),
            sa.ForeignKey("provider_accounts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "model_alias_id",
            sa.String(64),
            sa.ForeignKey("model_aliases.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("billing_unit", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("unit_scale", sa.Integer, nullable=False),
        sa.Column("request_price", MONEY, nullable=True),
        sa.Column("input_price", MONEY, nullable=True),
        sa.Column("output_price", MONEY, nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "billing_unit IN ('request', 'token')", name="ck_price_snapshots_billing_unit"
        ),
        sa.CheckConstraint("unit_scale >= 1", name="ck_price_snapshots_unit_scale_positive"),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_price_snapshots_currency_format"
        ),
        sa.CheckConstraint(
            "request_price IS NULL OR request_price >= 0",
            name="ck_price_snapshots_request_price_nonneg",
        ),
        sa.CheckConstraint(
            "input_price IS NULL OR input_price >= 0",
            name="ck_price_snapshots_input_price_nonneg",
        ),
        sa.CheckConstraint(
            "output_price IS NULL OR output_price >= 0",
            name="ck_price_snapshots_output_price_nonneg",
        ),
        *_timestamps(),
    )

    # Optional project-level budget policies.
    op.create_table(
        "project_budget_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("limit_amount", MONEY, nullable=False),
        sa.Column("window_seconds", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint("limit_amount > 0", name="ck_budget_policies_limit_positive"),
        sa.CheckConstraint("window_seconds >= 1", name="ck_budget_policies_window_positive"),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_budget_policies_currency_format"
        ),
        *_timestamps(),
    )

    # Authoritative budget-window committed/reserved amounts.
    op.create_table(
        "budget_windows",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "budget_policy_id",
            sa.String(64),
            sa.ForeignKey("project_budget_policies.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("committed_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("reserved_amount", MONEY, nullable=False, server_default="0"),
        *_timestamps(),
    )
    op.create_unique_constraint(
        "uq_budget_windows_policy_start", "budget_windows", ["budget_policy_id", "window_start"]
    )
    op.create_check_constraint(
        "ck_budget_windows_committed_nonneg", "budget_windows", "committed_amount >= 0"
    )
    op.create_check_constraint(
        "ck_budget_windows_reserved_nonneg", "budget_windows", "reserved_amount >= 0"
    )

    # Per-request monetary reservations against a budget policy window.
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(64),
            sa.ForeignKey("inference_requests.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "budget_policy_id",
            sa.String(64),
            sa.ForeignKey("project_budget_policies.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "price_snapshot_id",
            sa.String(64),
            sa.ForeignKey("price_snapshots.id"),
            nullable=False,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_amount", MONEY, nullable=False),
        sa.Column("committed_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("state", sa.String(32), nullable=False, server_default="reserved"),
        sa.Column("settlement_reason", sa.String(64), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_check_constraint(
        "ck_budget_reservations_reserved_nonneg", "budget_reservations", "reserved_amount >= 0"
    )
    op.create_check_constraint(
        "ck_budget_reservations_committed_nonneg", "budget_reservations", "committed_amount >= 0"
    )
    op.create_check_constraint(
        "ck_budget_reservations_state",
        "budget_reservations",
        "state IN ('reserved', 'committed', 'released')",
    )

    # Immutable measured-usage records (one per request).
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(64),
            sa.ForeignKey("inference_requests.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "execution_attempt_id",
            sa.String(64),
            sa.ForeignKey("execution_attempts.id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "principal_id", sa.String(64), sa.ForeignKey("principals.id"), nullable=True
        ),
        sa.Column(
            "api_credential_id",
            sa.String(64),
            sa.ForeignKey("api_credentials.id"),
            nullable=True,
        ),
        sa.Column(
            "model_alias_id",
            sa.String(64),
            sa.ForeignKey("model_aliases.id"),
            nullable=False,
        ),
        sa.Column(
            "route_binding_id",
            sa.String(64),
            sa.ForeignKey("route_bindings.id"),
            nullable=False,
        ),
        sa.Column(
            "provider_account_id",
            sa.String(64),
            sa.ForeignKey("provider_accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "price_snapshot_id",
            sa.String(64),
            sa.ForeignKey("price_snapshots.id"),
            nullable=False,
        ),
        sa.Column("billing_unit", sa.String(32), nullable=False),
        sa.Column("input_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("request_units", sa.Integer, nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("upstream_request_id", sa.String(255), nullable=True),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_usage_records_request", "usage_records", ["request_id"])
    op.create_check_constraint("ck_usage_records_amount_nonneg", "usage_records", "amount >= 0")
    op.create_check_constraint("ck_usage_records_input_nonneg", "usage_records", "input_units >= 0")
    op.create_check_constraint(
        "ck_usage_records_output_nonneg", "usage_records", "output_units >= 0"
    )
    op.create_check_constraint(
        "ck_usage_records_currency_format", "usage_records", "currency ~ '^[A-Z]{3}$'"
    )
    op.create_check_constraint(
        "ck_usage_records_billing_unit", "usage_records", "billing_unit IN ('request', 'token')"
    )

    # Append-only monetary ledger.
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "usage_record_id", sa.String(64), sa.ForeignKey("usage_records.id"), nullable=True
        ),
        sa.Column("entry_type", sa.String(32), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        *_timestamps(),
    )
    op.create_unique_constraint(
        "uq_ledger_entries_idempotency", "ledger_entries", ["idempotency_key"]
    )
    op.create_check_constraint(
        "ck_ledger_entries_type",
        "ledger_entries",
        "entry_type IN ('usage_debit', 'adjustment_credit', 'adjustment_debit')",
    )
    op.create_check_constraint(
        "ck_ledger_entries_currency_format", "ledger_entries", "currency ~ '^[A-Z]{3}$'"
    )

    # Request -> captured price snapshot (non-content accounting metadata).
    op.add_column(
        "inference_requests",
        sa.Column(
            "price_snapshot_id",
            sa.String(64),
            sa.ForeignKey("price_snapshots.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_inference_requests_price_snapshot_id",
        "inference_requests",
        ["price_snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_inference_requests_price_snapshot_id", table_name="inference_requests")
    op.drop_column("inference_requests", "price_snapshot_id")

    op.drop_constraint("ck_ledger_entries_currency_format", "ledger_entries", type_="check")
    op.drop_constraint("ck_ledger_entries_type", "ledger_entries", type_="check")
    op.drop_constraint("uq_ledger_entries_idempotency", "ledger_entries", type_="unique")
    op.drop_table("ledger_entries")

    op.drop_constraint("ck_usage_records_billing_unit", "usage_records", type_="check")
    op.drop_constraint("ck_usage_records_currency_format", "usage_records", type_="check")
    op.drop_constraint("ck_usage_records_output_nonneg", "usage_records", type_="check")
    op.drop_constraint("ck_usage_records_input_nonneg", "usage_records", type_="check")
    op.drop_constraint("ck_usage_records_amount_nonneg", "usage_records", type_="check")
    op.drop_constraint("uq_usage_records_request", "usage_records", type_="unique")
    op.drop_table("usage_records")

    op.drop_constraint("ck_budget_reservations_state", "budget_reservations", type_="check")
    op.drop_constraint(
        "ck_budget_reservations_committed_nonneg", "budget_reservations", type_="check"
    )
    op.drop_constraint(
        "ck_budget_reservations_reserved_nonneg", "budget_reservations", type_="check"
    )
    op.drop_table("budget_reservations")

    op.drop_constraint("ck_budget_windows_reserved_nonneg", "budget_windows", type_="check")
    op.drop_constraint("ck_budget_windows_committed_nonneg", "budget_windows", type_="check")
    op.drop_constraint("uq_budget_windows_policy_start", "budget_windows", type_="unique")
    op.drop_table("budget_windows")

    op.drop_constraint(
        "ck_budget_policies_currency_format", "project_budget_policies", type_="check"
    )
    op.drop_constraint(
        "ck_budget_policies_window_positive", "project_budget_policies", type_="check"
    )
    op.drop_constraint(
        "ck_budget_policies_limit_positive", "project_budget_policies", type_="check"
    )
    op.drop_table("project_budget_policies")

    op.drop_constraint(
        "ck_price_snapshots_output_price_nonneg", "price_snapshots", type_="check"
    )
    op.drop_constraint(
        "ck_price_snapshots_input_price_nonneg", "price_snapshots", type_="check"
    )
    op.drop_constraint(
        "ck_price_snapshots_request_price_nonneg", "price_snapshots", type_="check"
    )
    op.drop_constraint("ck_price_snapshots_currency_format", "price_snapshots", type_="check")
    op.drop_constraint("ck_price_snapshots_unit_scale_positive", "price_snapshots", type_="check")
    op.drop_constraint("ck_price_snapshots_billing_unit", "price_snapshots", type_="check")
    op.drop_table("price_snapshots")

    op.drop_constraint(
        "ck_price_policies_output_price_nonneg", "price_policies", type_="check"
    )
    op.drop_constraint(
        "ck_price_policies_input_price_nonneg", "price_policies", type_="check"
    )
    op.drop_constraint(
        "ck_price_policies_request_price_nonneg", "price_policies", type_="check"
    )
    op.drop_constraint("ck_price_policies_currency_format", "price_policies", type_="check")
    op.drop_constraint("ck_price_policies_unit_scale_positive", "price_policies", type_="check")
    op.drop_constraint("ck_price_policies_billing_unit", "price_policies", type_="check")
    op.drop_table("price_policies")
