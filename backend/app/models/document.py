import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt


class DocumentKind(StrEnum):
    LECTURE = "lecture"
    ASSIGNMENT = "assignment"
    NOTE = "note"
    SYLLABUS = "syllabus"


class OccurredAtSource(StrEnum):
    PARSED_SYLLABUS = "parsed_syllabus"
    INFERRED_FILENAME = "inferred_filename"
    MANUAL = "manual"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Document(Base, CreatedAt):
    __tablename__ = "documents"
    __table_args__ = (
        # Makes (id, user_id) a referenceable key so `chunks` can pin ownership
        # with a composite FK. Redundant on its own -- id is already unique.
        UniqueConstraint("id", "user_id", name="uq_documents_id_user_id"),
        # Document identity for re-ingestion: the same file path ingested twice
        # is the same document, not a second one.
        UniqueConstraint("user_id", "storage_path", name="uq_documents_user_id_storage_path"),
        CheckConstraint(
            "kind IN ('lecture', 'assignment', 'note', 'syllabus')",
            name="ck_documents_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_documents_status",
        ),
        CheckConstraint(
            "occurred_at_source IN ('parsed_syllabus', 'inferred_filename', 'manual')",
            name="ck_documents_occurred_at_source",
        ),
        # We either know when this happened and how we know, or neither. Storing a
        # date without its provenance is what `occurred_at_source` exists to stop.
        CheckConstraint(
            "(occurred_at IS NULL) = (occurred_at_source IS NULL)",
            name="ck_documents_occurred_at_has_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id"), nullable=False
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)

    # A local filesystem path in Phase 1. Supabase Storage lands later; the column
    # is the same either way.
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)

    # Invariant 1, second half: when the learning actually happened, as opposed to
    # when we inserted the row. Both nullable until Phase 3 does dating -- Phase 1
    # has no inference and will not invent a date. Invariant 4: from Phase 3 on,
    # `redate_document` is the only thing that writes this column.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurred_at_source: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
