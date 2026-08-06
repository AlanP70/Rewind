"""Queries against `chunks`. The only place chunk SQL is written."""

import uuid
from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk
from app.schemas.ingestion import PlannedChunk


async def list_for_document(session: AsyncSession, document_id: uuid.UUID) -> list[Chunk]:
    """Every chunk of a document, in reading order."""
    result = await session.execute(
        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
    )
    return list(result.scalars().all())


async def list_unembedded(session: AsyncSession, document_id: uuid.UUID) -> list[Chunk]:
    """Chunks still missing a vector, in reading order.

    This is the work list for embedding, and the reason a failed run resumes
    instead of restarting: whatever was already committed is no longer selected,
    so no chunk is ever paid for twice.
    """
    result = await session.execute(
        select(Chunk)
        .where(Chunk.document_id == document_id, Chunk.embedding.is_(None))
        .order_by(Chunk.chunk_index)
    )
    return list(result.scalars().all())


async def count_unembedded(session: AsyncSession, document_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.document_id == document_id, Chunk.embedding.is_(None))
    )
    return result.scalar_one()


async def count_for_document(session: AsyncSession, document_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
    )
    return result.scalar_one()


async def delete_for_document(session: AsyncSession, document_id: uuid.UUID) -> int:
    """Remove every chunk of a document. Returns how many went.

    This is what a `--force` re-ingest runs before re-chunking. It must happen in
    the same transaction as the re-insert, or a crash in between leaves a
    document with no chunks and a `ready` status.
    """
    result = await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    return result.rowcount


async def insert_many(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    planned: Iterable[PlannedChunk],
) -> None:
    """Write planned chunks. `embedding` stays null -- that step is separate."""
    session.add_all(
        [
            Chunk(
                user_id=user_id,
                document_id=document_id,
                content=chunk.content,
                page_number=chunk.page_number,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                chunk_index=chunk.chunk_index,
            )
            for chunk in planned
        ]
    )
    await session.flush()
