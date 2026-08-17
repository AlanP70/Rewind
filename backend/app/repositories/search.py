"""Nearest-neighbour queries over `chunks`. The only place search SQL is written.

No index yet -- Phase 4 slice 3 adds HNSW. Until then this is a sequential scan
and a sort over every embedded chunk the user owns, which is the point: the
latency is measured here first so the index can be shown to have done something.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Course, Document, OccurredAtSource


@dataclass(frozen=True)
class ChunkHit:
    """One chunk, its document, and how far it sits from the query.

    A dataclass rather than a `Chunk` because a hit is not a row: the document
    and course columns come from the join, and `distance` exists only relative to
    one query. Returning ORM objects would make the caller re-fetch all of it.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    course_id: uuid.UUID
    document_title: str
    course_name: str
    storage_key: str
    page_number: int
    char_start: int
    char_end: int
    content: str
    distance: float
    occurred_at: datetime | None
    occurred_at_source: OccurredAtSource | None


async def nearest_chunks(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    embedding: list[float],
    limit: int,
    course_id: uuid.UUID | None = None,
) -> list[ChunkHit]:
    """The `limit` chunks closest to `embedding`, nearest first.

    **Cosine distance** (`<=>`), which is what the HNSW index in slice 3 must also
    be built for -- an index built on a different operator class is simply not
    used by this query, and the only visible symptom is that the index made no
    difference. `text-embedding-3-small` returns normalised vectors, so cosine
    and inner product would rank identically; cosine is chosen because its range
    is fixed and `1 - distance` is therefore a meaningful similarity to show a
    person.

    `embedding.is_not(None)` is not an optimisation. A chunk with no vector is a
    document that is still being processed, and pgvector sorts null distances
    last rather than dropping them -- so without this, a `limit` larger than the
    number of embedded chunks pads the results with rows that were never ranked.

    `course_id` is optional and defaults to every course. Searching a whole degree
    is the product; scoping to one course is the special case.
    """
    query = (
        select(
            Chunk.id,
            Chunk.document_id,
            Document.course_id,
            Document.title,
            Course.name,
            Document.storage_key,
            Chunk.page_number,
            Chunk.char_start,
            Chunk.char_end,
            Chunk.content,
            Chunk.embedding.cosine_distance(embedding).label("distance"),
            Document.occurred_at,
            Document.occurred_at_source,
        )
        .join(Document, Document.id == Chunk.document_id)
        .join(Course, Course.id == Document.course_id)
        .where(Chunk.user_id == user_id, Chunk.embedding.is_not(None))
        .order_by("distance")
        .limit(limit)
    )

    if course_id is not None:
        query = query.where(Document.course_id == course_id)

    return [ChunkHit(*row) for row in (await session.execute(query)).all()]


@dataclass(frozen=True)
class CorpusSize:
    embedded_chunks: int
    documents: int


async def count_embedded_chunks(
    session: AsyncSession, *, user_id: uuid.UUID, course_id: uuid.UUID | None = None
) -> CorpusSize:
    """How much `nearest_chunks` had to look at. The denominator of any latency
    number -- 216 chunks and 216,000 chunks are different claims about the same
    milliseconds, and only one of them says anything about an index."""
    query = (
        select(
            func.count(Chunk.id),
            func.count(func.distinct(Chunk.document_id)),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.user_id == user_id, Chunk.embedding.is_not(None))
    )
    if course_id is not None:
        query = query.where(Document.course_id == course_id)

    chunks, documents = (await session.execute(query)).one()
    return CorpusSize(embedded_chunks=chunks, documents=documents)
