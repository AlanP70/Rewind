"""Queries against `documents`. The only place document SQL is written."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document


async def get(
    session: AsyncSession, document_id: uuid.UUID, user_id: uuid.UUID
) -> Document | None:
    result = await session.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def set_status(session: AsyncSession, document_id: uuid.UUID, status: str) -> None:
    """Set status by statement rather than by mutating an ORM object.

    The failure path runs this straight after a rollback, which expires everything
    in the session; an UPDATE does not care whether the identity map survived.
    """
    await session.execute(
        update(Document).where(Document.id == document_id).values(status=status)
    )


async def get_by_storage_path(
    session: AsyncSession, user_id: uuid.UUID, storage_path: str
) -> Document | None:
    """Look a document up by the identity re-ingestion uses.

    `UNIQUE (user_id, storage_path)` is what makes the same file ingested twice
    the same document rather than a second one, so this is the query that decides
    whether a run is a first ingest or a re-ingest.
    """
    result = await session.execute(
        select(Document).where(
            Document.user_id == user_id, Document.storage_path == storage_path
        )
    )
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    kind: str,
    title: str,
    storage_path: str,
) -> Document:
    document = Document(
        user_id=user_id,
        course_id=course_id,
        kind=kind,
        title=title,
        storage_path=storage_path,
    )
    session.add(document)
    await session.flush()
    return document
