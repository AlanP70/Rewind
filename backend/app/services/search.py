"""Vector search: a question in, ranked chunks out.

Thin by design. The two halves that could hold a bug live elsewhere -- the model
choice in `services/embedding.py`, the SQL in `repositories/search.py` -- and
what is left here is the order of operations and the measurement.
"""

import re
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import search as search_repo
from app.schemas.search import SearchHit, SearchResults, TimelineResults
from app.services.embedding import embed_query
from app.services.errors import ServiceError
from app.services.timeline import build_timeline, timeline_results


async def search(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    limit: int = 20,
    course_id: uuid.UUID | None = None,
    embedding: list[float] | None = None,
) -> SearchResults:
    """Rank every embedded chunk this user owns against `query`, nearest first.

    `embedding` lets a caller supply the query vector it already has. The eval
    uses it to cache 16 vectors across runs, so re-running a comparison costs
    nothing and cannot drift underneath it. It is an input to this function
    rather than a mode of it: the ranking is identical either way, and the only
    thing that changes is whether `embed_ms` measures a round trip or reports the
    0.0 it actually took.
    """
    if not query.strip():
        raise ServiceError("a search needs a query")

    # `perf_counter`, not `time()`: this is a duration, and the wall clock can
    # step sideways mid-measurement.
    started = time.perf_counter()
    if embedding is None:
        embedding = await embed_query(query)
    embedded_at = time.perf_counter()

    hits = await search_repo.nearest_chunks(
        session,
        user_id=user_id,
        embedding=embedding,
        limit=limit,
        course_id=course_id,
    )
    finished = time.perf_counter()

    return SearchResults(
        query=query,
        hits=[
            SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                course_id=hit.course_id,
                document_title=hit.document_title,
                course_name=hit.course_name,
                page_number=hit.page_number,
                char_start=hit.char_start,
                char_end=hit.char_end,
                content=hit.content,
                distance=hit.distance,
                occurred_at=hit.occurred_at,
                occurred_at_source=hit.occurred_at_source,
            )
            for hit in hits
        ],
        embed_ms=(embedded_at - started) * 1000,
        query_ms=(finished - embedded_at) * 1000,
    )


async def search_timeline(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    limit: int = 20,
    course_id: uuid.UUID | None = None,
) -> TimelineResults:
    """What `POST /search` actually answers: the same ranking, shaped as a timeline.

    Two steps and no third. `search` ranks chunks, `build_timeline` groups them
    per document and decides the badge -- the same function slice 4's eval scores,
    so the number reported and the badge shipped come from one implementation.

    `limit` is still chunks, not documents: 20 chunks may be five documents or
    twenty, and `documents_considered` reports which. Nothing between here and the
    response drops a document, so that count is the size of the set the badge is a
    claim about.
    """
    results = await search(
        session, user_id=user_id, query=query, limit=limit, course_id=course_id
    )
    return timeline_results(
        build_timeline(results.hits),
        query=results.query,
        embed_ms=results.embed_ms,
        query_ms=results.query_ms,
    )


# A conventional English stop list, not one derived from the eval's questions --
# tuning this to the questions would be tuning the baseline to the test.
#
# One consequence is worth naming rather than discovering in the results:
# `first` is on it, as it is on most standard lists, and this corpus contains
# `breadth-first search` and `depth-first search`. So those two queries reach the
# baseline as {breadth, search} and {depth, search}. That weakens the baseline
# slightly on exactly two of sixteen questions, in the direction of flattering
# vector search, and it is left alone because the alternative is editing the stop
# list against the answer sheet.
STOPWORDS = frozenset(
    """
    a about all also an and any are as at be been but by can come could do does
    did each first for from get go had has have he her him his how i if in into
    is it its just like make may me more most my no not of on one only or other
    our out over said same see she should so some take than that the their them
    then there these they this those to up us use want was way we well were what
    when where which while who will with would you your
    """.split()
)

_WORD = re.compile(r"[a-z0-9]+")


def keyword_terms(query: str) -> list[str]:
    """The terms `keyword_chunks` searches for: lowercased, destopped, deduped.

    Single characters go too, which is what turns `Dijkstra's` into `dijkstra`
    rather than into `dijkstra` plus a stray `s` that matches every chunk in the
    corpus.
    """
    seen: dict[str, None] = {}
    for word in _WORD.findall(query.lower()):
        if len(word) > 1 and word not in STOPWORDS:
            seen[word] = None
    return list(seen)


async def keyword_search(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    limit: int = 20,
    course_id: uuid.UUID | None = None,
) -> SearchResults:
    """The baseline. Same signature, same return type, same rows -- different ranking.

    Returning `SearchResults` rather than something baseline-shaped is the point:
    the eval scores both rankers through one code path, so a scoring bug cannot
    favour one of them. `embed_ms` is 0.0 because nothing is embedded, which is
    also the honest number -- not embedding is most of why this is fast.
    """
    if not query.strip():
        raise ServiceError("a search needs a query")

    started = time.perf_counter()
    hits = await search_repo.keyword_chunks(
        session,
        user_id=user_id,
        terms=keyword_terms(query),
        limit=limit,
        course_id=course_id,
    )
    finished = time.perf_counter()

    return SearchResults(
        query=query,
        hits=[
            SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                course_id=hit.course_id,
                document_title=hit.document_title,
                course_name=hit.course_name,
                page_number=hit.page_number,
                char_start=hit.char_start,
                char_end=hit.char_end,
                content=hit.content,
                distance=hit.distance,
                occurred_at=hit.occurred_at,
                occurred_at_source=hit.occurred_at_source,
            )
            for hit in hits
        ],
        embed_ms=0.0,
        query_ms=(finished - started) * 1000,
    )
