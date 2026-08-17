"""Ask a question, get ranked passages.

Translation only, like the other routes: parse, call one service, map a
`ServiceError` to a status code.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.models.user import SEED_USER_ID
from app.schemas.search import SearchRequest, SearchResults
from app.services.errors import ServiceError
from app.services.search import search

router = APIRouter(tags=["search"])


@router.post("/search")
async def search_chunks(
    session: Annotated[AsyncSession, Depends(get_session)],
    body: SearchRequest,
) -> SearchResults:
    """Rank chunks against a query. **POST, and it writes nothing.**

    Not a violation of POST-means-write so much as an acceptance of the usual
    trade: the query is user-typed prose that belongs in a body, and nothing here
    is a resource a URL could name. Nothing is stored -- no search history table
    exists, and the day one does it will be a separate write with its own reason.

    `user_id` is the seed user until Phase 7. Scoping every hit by owner is
    already in the query rather than waiting for auth, because a search that
    forgets it is how one student's material appears in another's timeline.
    """
    try:
        return await search(
            session,
            user_id=SEED_USER_ID,
            query=body.query,
            limit=body.limit,
            course_id=body.course_id,
        )
    except ServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
