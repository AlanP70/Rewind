"""The re-ingest guard, carried over from Phase 1.

Phase 1 verified this by hand and deferred the automated version until database
fixtures existed for another reason -- so the setup cost was amortised rather
than paid for a single test. Phase 2 built them, so here it is.

What is being pinned: re-ingesting the same file is the *same document* with its
chunks rebuilt, never a second document and never a doubled chunk count. The
failure this guards against is silent in the worst way, because a document with
18 chunks instead of 9 still searches, still renders, and just quietly returns
everything twice.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import LocalStorage, storage_key
from app.models.user import SEED_USER_ID
from app.repositories import chunks as chunks_repo
from app.services.errors import ServiceError
from app.services.ingestion import create_course, ingest_document, resolve_document
from tests.conftest import LECTURE

KEY = storage_key(SEED_USER_ID, LECTURE.name)


async def _course(session: AsyncSession) -> uuid.UUID:
    course = await create_course(
        session,
        user_id=SEED_USER_ID,
        name="Introduction to Algorithms",
        starts_on=date(2024, 9, 1),
        ends_on=date(2024, 12, 15),
    )
    await session.commit()
    return course.id


async def _ingest(session: AsyncSession, course_id: uuid.UUID, *, force: bool = False) -> int:
    resolved = await _resolve(session, course_id, force=force)
    result = await ingest_document(session, resolved=resolved, data=LECTURE.read_bytes())
    await session.commit()
    return result.chunk_count


async def _resolve(session: AsyncSession, course_id: uuid.UUID, *, force: bool = True):
    """Look the document up the way ingestion does, by its storage key."""
    return await resolve_document(
        session,
        user_id=SEED_USER_ID,
        course_id=course_id,
        storage_key=KEY,
        kind="lecture",
        title="Depth-First Search",
        force=force,
    )


async def test_reingest_without_force_refuses(session: AsyncSession) -> None:
    course_id = await _course(session)
    await _ingest(session, course_id)

    with pytest.raises(ServiceError, match="set force"):
        await _ingest(session, course_id, force=False)


async def test_reingest_with_force_replaces_rather_than_duplicates(
    session: AsyncSession,
) -> None:
    course_id = await _course(session)
    first = await _ingest(session, course_id)
    second = await _ingest(session, course_id, force=True)
    third = await _ingest(session, course_id, force=True)

    assert first == second == third
    # The point of the test: three ingests, one document's worth of chunks.
    stored = await chunks_repo.count_for_document(
        session, (await _resolve(session, course_id)).document.id
    )
    assert stored == first


async def test_reingest_is_the_same_document(session: AsyncSession) -> None:
    course_id = await _course(session)
    await _ingest(session, course_id)
    first = (await _resolve(session, course_id)).document.id

    await _ingest(session, course_id, force=True)
    second = (await _resolve(session, course_id)).document.id

    assert first == second


async def test_the_same_file_under_a_different_name_is_a_different_document(
    session: AsyncSession, storage: LocalStorage
) -> None:
    """The accepted cost of keying on the filename rather than on content.

    Pinned rather than merely documented, because the alternative -- content
    addressing -- has the opposite and worse failure: a re-exported lecture would
    become a second document and silently orphan the first, splitting a
    semester's timeline for a concept in two.
    """
    course_id = await _course(session)
    await _ingest(session, course_id)

    renamed = await resolve_document(
        session,
        user_id=SEED_USER_ID,
        course_id=course_id,
        storage_key=storage_key(SEED_USER_ID, "DFS (copy).pdf"),
        kind="lecture",
        title="Depth-First Search",
    )

    assert renamed.reused is False
    assert renamed.document.id != (await _resolve(session, course_id)).document.id


def test_the_fixture_pdf_is_present() -> None:
    """A clean clone must be able to run this suite.

    Phase 1 committed the corpus for exactly this reason; if it ever stops being
    committed, every test above fails with a confusing extraction error rather
    than saying what is actually wrong.
    """
    assert LECTURE.is_file(), f"missing test corpus: {LECTURE}"
