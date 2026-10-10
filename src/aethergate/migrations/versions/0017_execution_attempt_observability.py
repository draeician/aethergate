"""observability: durable first-token and upstream-outcome metadata

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-09

Adds safe, non-content execution-attempt metadata so operational observability
(queue wait, streaming TTFT, retry rate, passive upstream health) can be derived
from durable facts:

- ``first_token_at`` — timestamp when the worker received the first non-empty
  provider content chunk for a streaming attempt (NULL when never observed);
- ``upstream_error`` — true only when the terminal failure was a ``ProviderError``
  (secret/config/unsupported-provider failures stay false);
- ``upstream_status_code`` — sanitized numeric upstream HTTP status when reliably
  known (never a header, URL, body, or provider response content).

No prompt/completion content, provider secret material, or raw exception text is
stored. Historical rows are backfilled with no data: ``first_token_at`` is NULL,
``upstream_error`` is false, ``upstream_status_code`` is NULL.

Indexes are limited to those justified by the bounded rolling-window aggregate
queries: attempt start time (TTFT/retry windows), endpoint + completion time
(passive upstream-health grouping), and request enqueue time (queue-wait/retry
cohorts).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_attempts",
        sa.Column("first_token_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "execution_attempts",
        sa.Column(
            "upstream_error",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "execution_attempts",
        sa.Column("upstream_status_code", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_execution_attempts_started_at",
        "execution_attempts",
        ["started_at"],
    )
    op.create_index(
        "ix_execution_attempts_endpoint_finished",
        "execution_attempts",
        ["endpoint_id", "finished_at"],
    )
    op.create_index(
        "ix_inference_requests_queued_at",
        "inference_requests",
        ["queued_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_inference_requests_queued_at", table_name="inference_requests")
    op.drop_index(
        "ix_execution_attempts_endpoint_finished", table_name="execution_attempts"
    )
    op.drop_index("ix_execution_attempts_started_at", table_name="execution_attempts")
    op.drop_column("execution_attempts", "upstream_status_code")
    op.drop_column("execution_attempts", "upstream_error")
    op.drop_column("execution_attempts", "first_token_at")
