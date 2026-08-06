import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt


class RunStatus(StrEnum):
    """Deliberately disjoint from `DocumentStatus`.

    No value appears in both, so code that reads one where it meant the other
    fails a CHECK constraint instead of quietly comparing false forever.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATUSES = (RunStatus.SUCCEEDED, RunStatus.FAILED)


class ProcessingRun(Base, CreatedAt):
    """One row per processing attempt. History, not current state.

    `documents.status` answers "where is this document now". This table answers
    "what has been tried, what went wrong, and did a retry fix it" -- which is
    the only question worth asking about a PDF that failed to parse unattended.

    No `occurred_at`, despite invariant 1: a processing run is bookkeeping about
    the system, not a record of learning. `created_at` is the whole truth of when
    it happened.
    """

    __tablename__ = "processing_runs"
    __table_args__ = (
        # A run cannot be attached to another user's document. Same pairing as
        # `chunks` -- ownership is checked by Postgres, not by convention.
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_processing_runs_document_id_user_id",
        ),
        # Natural key, and the index the "latest run for this document" lookup
        # rides on. Also makes a double-write of the same attempt number an error
        # rather than two rows claiming to be attempt 2.
        UniqueConstraint(
            "document_id", "attempts", name="uq_processing_runs_document_id_attempts"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_processing_runs_status",
        ),
        CheckConstraint("attempts >= 1", name="ck_processing_runs_attempts_positive"),
        # The three rules that keep a row's timestamps agreeing with its status.
        # Without them a crashed worker's row is indistinguishable from a
        # succeeded one that forgot to write `finished_at`.
        CheckConstraint(
            "(status = 'queued') = (started_at IS NULL)",
            name="ck_processing_runs_started_at_matches_status",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name="ck_processing_runs_finished_at_matches_status",
        ),
        # An error on a run that succeeded is a contradiction, and the direction
        # that matters: a failed run with no error is a dead end for whoever is
        # debugging it at 2am.
        CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)",
            name="ck_processing_runs_error_matches_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # No direct FK to users: the composite FK above pins this to the document's
    # owner, and the document is already tied to a real user.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    # The attempt number of *this* row, 1-based -- not a counter incremented in
    # place. Attempt 3 is a third row carrying its own error and its own timings,
    # so a document that failed twice differently and then succeeded can still
    # say so. Monotonic per document: a later re-upload continues the numbering
    # rather than restarting it.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)

    # Populated only on failure, and always on failure -- see the CHECK above.
    error: Mapped[str | None] = mapped_column(Text)

    # Null while queued. `finished_at` stays null for a run whose worker was
    # killed, which is exactly what makes a stuck run detectable.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
