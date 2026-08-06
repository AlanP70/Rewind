"""processing runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-06

Processing history: one row per attempt at turning a document into chunks and
vectors.

Written by hand, same as 0002 and for the same reason -- the composite foreign
key and the status/timestamp CHECK constraints are the point of the table, and
autogenerate emits both inconsistently.

`documents` is untouched. Its existing four statuses already cover the queue:
`pending` is "uploaded, nothing started", `processing` is "a worker has it".

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processing_runs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        # The attempt number of this row, 1-based. Not a counter bumped in place:
        # each attempt keeps its own error and timings.
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # Ownership is pinned to the document's owner, by the database.
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_processing_runs_document_id_user_id",
        ),
        # Natural key, and the index "latest run for this document" reads.
        sa.UniqueConstraint(
            "document_id", "attempts", name="uq_processing_runs_document_id_attempts"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_processing_runs_status",
        ),
        sa.CheckConstraint("attempts >= 1", name="ck_processing_runs_attempts_positive"),
        # Timestamps must agree with status, or a crashed worker's row is
        # indistinguishable from a finished one that failed to write finished_at.
        sa.CheckConstraint(
            "(status = 'queued') = (started_at IS NULL)",
            name="ck_processing_runs_started_at_matches_status",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name="ck_processing_runs_finished_at_matches_status",
        ),
        # Both directions: no error on a success, and never a failure without one.
        sa.CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)",
            name="ck_processing_runs_error_matches_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("processing_runs")
