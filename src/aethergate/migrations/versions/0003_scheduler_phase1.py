"""durable scheduler phase 1

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


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
    op.add_column(
        "endpoints",
        sa.Column("max_concurrency", sa.Integer, nullable=False, server_default="1"),
    )

    op.create_table(
        "inference_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("projects.id"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "principal_id",
            sa.String(64),
            sa.ForeignKey("principals.id"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "api_credential_id",
            sa.String(64),
            sa.ForeignKey("api_credentials.id"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "model_alias_id",
            sa.String(64),
            sa.ForeignKey("model_aliases.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "endpoint_id",
            sa.String(64),
            sa.ForeignKey("endpoints.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("state", sa.String(32), nullable=False, index=True),
        sa.Column("stream", sa.Boolean, nullable=False),
        sa.Column("payload_encrypted", sa.LargeBinary, nullable=False),
        sa.Column("result_encrypted", sa.LargeBinary, nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queue_wait_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancellation_requested", sa.Boolean, nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("fencing_token", sa.BigInteger, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "reservations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(64),
            sa.ForeignKey("inference_requests.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "endpoint_id",
            sa.String(64),
            sa.ForeignKey("endpoints.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("requested_units", sa.Integer, nullable=False),
        sa.Column("granted_units", sa.Integer, nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "execution_attempts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(64),
            sa.ForeignKey("inference_requests.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "endpoint_id",
            sa.String(64),
            sa.ForeignKey("endpoints.id"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "reservation_id",
            sa.String(64),
            sa.ForeignKey("reservations.id"),
            nullable=True,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("fencing_token", sa.BigInteger, nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upstream_request_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "stream_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(64),
            sa.ForeignKey("inference_requests.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("event_encrypted", sa.LargeBinary, nullable=False),
        *_timestamps(),
    )
    op.create_unique_constraint(
        "uq_stream_events_request_seq", "stream_events", ["request_id", "seq"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_stream_events_request_seq", "stream_events", type_="unique")
    op.drop_table("stream_events")
    op.drop_table("execution_attempts")
    op.drop_table("reservations")
    op.drop_table("inference_requests")
    op.drop_column("endpoints", "max_concurrency")
