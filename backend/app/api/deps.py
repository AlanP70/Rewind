"""Dependencies shared by more than one router.

Here rather than on whichever router declared them first: a second router
importing `get_session` from `api/documents.py` makes the two routers depend on
each other for no reason, and the direction of that import is arbitrary.
"""

from collections.abc import AsyncIterator

from arq.connections import ArqRedis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


def get_queue(request: Request) -> ArqRedis:
    """The pool opened in the app's lifespan. See `core/queue.py`."""
    return request.app.state.queue
