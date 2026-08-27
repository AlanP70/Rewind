"""Ranking queries over `chunks`. The only place search SQL is written.

Two rankers over the same rows: `nearest_chunks` is the product, and
`keyword_chunks` is the baseline it has to beat. They live side by side
deliberately -- the comparison is only worth anything if the row eligibility
rules are identical, and the cheapest way to keep them identical is to have to
look at both while changing either.

The HNSW index (migration `0006`) covers `nearest_chunks`. At 216 chunks the
planner does not choose it and every plan is a sequential scan; see ROADMAP's
"Settled in slice 3" for the measurement and for why it was committed anyway.
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


async def keyword_chunks(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    terms: list[str],
    limit: int,
    course_id: uuid.UUID | None = None,
) -> list[ChunkHit]:
    """The keyword baseline: `ILIKE`, ranked, over the same rows as `nearest_chunks`.

    This exists to be *beaten*, and it is only worth beating if it is not a
    strawman. Three choices are what keep it fair, and each one has an obvious
    lazier version that would hand vector search a free win:

    - **Any term, not all of them.** `AND` across every term returns nothing for
      a question phrased as a sentence, and a baseline that scores zero measures
      the person who wrote it.
    - **Ranked, not filtered.** Plain `ILIKE` is a predicate with no order, so a
      ranking has to be defined: how many distinct query terms the chunk
      contains, then how often they occur.
    - **The tie-break touches neither dates nor document order.** This matters
      specifically here, because the metric is *first* occurrence: a tie-break by
      `occurred_at` would let the baseline score well by guessing "earliest"
      rather than by matching anything. `Chunk.id` is a random v4 uuid, so it is
      deterministic per row and uncorrelated with when the document happened.

    Row eligibility is deliberately identical to `nearest_chunks`, including
    `embedding IS NOT NULL` -- which is not a filter this ranker needs, but
    scoring the two over different row sets would make the comparison meaningless
    in a way no test would catch.
    """
    if not terms:
        return []

    lowered = func.lower(Chunk.content)

    # How many distinct query terms appear at all. The primary ranking signal.
    matched = sum(
        case((Chunk.content.ilike(f"%{term}%"), 1), else_=0) for term in terms
    )

    # How many times, in total. Postgres has no occurrence count, so this is the
    # standard trick: delete every copy of the term and see how much shorter the
    # string got.
    frequency = sum(
        (func.length(lowered) - func.length(func.replace(lowered, term, ""))) / len(term)
        for term in terms
    )

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
            # Not a distance. The fraction of query terms the chunk is *missing*,
            # carried in the same field so that both rankers hand the eval and the
            # timeline an identical shape. 0.0 means every term was present.
            (1.0 - matched / float(len(terms))).label("distance"),
            Document.occurred_at,
            Document.occurred_at_source,
        )
        .join(Document, Document.id == Chunk.document_id)
        .join(Course, Course.id == Document.course_id)
        .where(Chunk.user_id == user_id, Chunk.embedding.is_not(None), matched > 0)
        .order_by(matched.desc(), frequency.desc(), Chunk.id)
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
