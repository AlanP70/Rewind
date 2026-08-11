"""Queries against `documents`. The only place document SQL is written."""

import uuid
from datetime import datetime

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


async def set_occurred_at(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    occurred_at: datetime,
    source: str,
) -> None:
    """The only statement in the codebase that writes `documents.occurred_at`.

    **Call this from `services.dating.redate_document` and nowhere else.** That is
    invariant 4, and it is not decoration: from Phase 5 on, every change to this
    column must also move `concept_mentions.occurred_at` for the same document in
    the same transaction, and no constraint can enforce that (ARCHITECTURE, "The
    asymmetry"). One function is where that obligation is attached, so a second
    caller here is a mention table that silently disagrees with its documents.

    `tests/test_occurred_at_sole_writer.py` fails if either half of that stops
    being true -- if another module writes the column, or if anything but
    `redate_document` calls this.

    The date and its provenance are set together in one statement. They are bound
    by `ck_documents_occurred_at_has_source`, so writing one without the other is
    a constraint violation rather than a subtly undated row.
    """
    await session.execute(
        update(Document)
        .where(Document.id == document_id, Document.user_id == user_id)
        .values(occurred_at=occurred_at, occurred_at_source=source)
    )


async def list_for_course(
    session: AsyncSession, *, course_id: uuid.UUID, user_id: uuid.UUID
) -> list[Document]:
    """Every document in one course, oldest row first.

    Ordered by `created_at`, not `occurred_at` -- the caller is the filename
    dater, and every document it cares about is one whose `occurred_at` is still
    null. Ordering by the column being filled in would sort by nothing.
    """
    result = await session.execute(
        select(Document)
        .where(Document.course_id == course_id, Document.user_id == user_id)
        .order_by(Document.created_at)
    )
    return list(result.scalars().all())


async def get_by_storage_key(
    session: AsyncSession, user_id: uuid.UUID, storage_key: str
) -> Document | None:
    """Look a document up by the identity re-ingestion uses.

    `UNIQUE (user_id, storage_key)` is what makes the same file ingested twice
    the same document rather than a second one, so this is the query that decides
    whether a run is a first ingest or a re-ingest.
    """
    result = await session.execute(
        select(Document).where(
            Document.user_id == user_id, Document.storage_key == storage_key
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
    storage_key: str,
) -> Document:
    document = Document(
        user_id=user_id,
        course_id=course_id,
        kind=kind,
        title=title,
        storage_key=storage_key,
    )
    session.add(document)
    await session.flush()
    return document
