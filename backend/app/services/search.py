"""Vector search: a question in, ranked chunks out.

Thin by design. The two halves that could hold a bug live elsewhere -- the model
choice in `services/embedding.py`, the SQL in `repositories/search.py` -- and
what is left here is the order of operations and the measurement.
"""

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import search as search_repo
from app.schemas.search import SearchHit, SearchResults
from app.services.embedding import embed_query
from app.services.errors import ServiceError


async def search(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    limit: int = 20,
    course_id: uuid.UUID | None = None,
) -> SearchResults:
    """Rank every embedded chunk this user owns against `query`, nearest first."""
    if not query.strip():
        raise ServiceError("a search needs a query")

    # `perf_counter`, not `time()`: this is a duration, and the wall clock can
    # step sideways mid-measurement.
    started = time.perf_counter()
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
