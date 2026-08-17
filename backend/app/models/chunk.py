import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt

# Matches OpenAI text-embedding-3-small. Changing the model means a migration
# plus a full re-embed, so the dimension is a commitment, not a setting.
EMBEDDING_DIMENSIONS = 1536


class Chunk(Base, CreatedAt):
    __tablename__ = "chunks"
    __table_args__ = (
        # A chunk cannot be attached to another user's document: Postgres checks
        # the pair, not just the id.
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_chunks_document_id_user_id",
        ),
        # Two referenceable keys, both consumed by `concept_mentions` in Phase 5.
        # They cannot be merged into UNIQUE (id, user_id, document_id): a composite
        # FK must reference exactly the columns of a unique constraint, never a
        # prefix or subset of a wider one.
        UniqueConstraint("id", "user_id", name="uq_chunks_id_user_id"),
        UniqueConstraint("id", "document_id", name="uq_chunks_id_document_id"),
        # Natural key. Also what makes a --force re-ingest fail loudly rather than
        # quietly doubling a document's chunks.
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_id_chunk_index"),
        # Invariant 3, as far as the database can enforce it. NOT NULL below says
        # the offsets exist; these say they are not nonsense. That
        # content == page_text[char_start:char_end] is not expressible here, which
        # is why `verify` exists.
        CheckConstraint("page_number >= 1", name="ck_chunks_page_number_positive"),
        CheckConstraint("chunk_index >= 0", name="ck_chunks_chunk_index_non_negative"),
        CheckConstraint("char_start >= 0", name="ck_chunks_char_start_non_negative"),
        CheckConstraint("char_end > char_start", name="ck_chunks_char_end_after_start"),
        # Cosine, matching the `<=>` in `repositories/search.py`. Declared here as
        # well as in migration 0006 so autogenerate does not propose dropping it;
        # the operator class is the part that has to agree with the query, and it
        # now has to be wrong in two places to be wrong at all.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # No direct FK to users: the composite FK above pins this to the document's
    # owner, and the document is already tied to a real user. The chain composes.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Null until the embedding step fills it. Indexed for cosine search by the
    # HNSW index in `__table_args__`; nulls are simply not in the index, which is
    # why the search query filters them out itself.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    # Invariant 3: never null, and never backfillable. `char_start`/`char_end`
    # index into the text of this one page, not into the whole document.
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ordering across the whole document, not restarting per page, so chunks sort
    # into reading order with one ORDER BY.
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
