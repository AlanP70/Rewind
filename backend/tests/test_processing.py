"""The `processing_runs` lifecycle.

Every test here runs with `embed=False`, which keeps the suite free of an OpenAI
key and of network calls. That is not a workaround: the run lifecycle is exactly
the part that has to be right when the expensive step is *not* what failed.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import LocalStorage, storage_key
from app.models import DocumentStatus, RunStatus
from app.models.user import SEED_USER_ID
from app.repositories import documents as documents_repo
from app.repositories import processing_runs as runs_repo
from app.services.errors import ServiceError
from app.services.ingestion import create_course
from app.services.processing import process_document
from tests.conftest import CORRUPT_PDF, LECTURE


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


async def _upload(storage: LocalStorage, filename: str, data: bytes) -> str:
    """Put bytes in storage the way the CLI and the upload endpoint both do.

    Uploading is the caller's job, not `process_document`'s, so the tests do it
    too rather than testing a path no entrypoint takes.
    """
    key = storage_key(SEED_USER_ID, filename)
    await storage.upload(key, data)
    return key


async def _process(session: AsyncSession, course_id: uuid.UUID, key: str, *, force: bool = False):
    return await process_document(
        session,
        user_id=SEED_USER_ID,
        course_id=course_id,
        storage_key=key,
        kind="lecture",
        title=key.rsplit("/", 1)[-1],
        force=force,
        embed=False,
    )


async def test_a_successful_run_is_recorded(
    session: AsyncSession, storage: LocalStorage
) -> None:
    course_id = await _course(session)
    key = await _upload(storage, LECTURE.name, LECTURE.read_bytes())
    result = await _process(session, course_id, key)

    runs = await runs_repo.list_for_document(session, result.document_id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.SUCCEEDED
    assert runs[0].attempts == 1
    assert runs[0].error is None
    assert runs[0].started_at is not None
    assert runs[0].finished_at is not None


async def test_embeddings_skipped_leaves_the_document_short_of_ready(
    session: AsyncSession, storage: LocalStorage
) -> None:
    """A document with chunks but no vectors cannot be searched, so it is not
    `ready`. Phase 1 set this rule; the run still counts as succeeded, because
    the attempt did what it was asked to do."""
    course_id = await _course(session)
    key = await _upload(storage, LECTURE.name, LECTURE.read_bytes())
    result = await _process(session, course_id, key)

    document = await documents_repo.get(session, result.document_id, SEED_USER_ID)
    assert document.status == DocumentStatus.PROCESSING
    assert result.embedding is None


async def test_a_corrupt_pdf_fails_the_run_and_the_document(
    session: AsyncSession, storage: LocalStorage
) -> None:
    course_id = await _course(session)
    key = await _upload(storage, "corrupt-fixture.pdf", CORRUPT_PDF)

    with pytest.raises(ServiceError, match="could not read"):
        await _process(session, course_id, key)

    document = await documents_repo.get_by_storage_key(session, SEED_USER_ID, key)
    assert document.status == DocumentStatus.FAILED

    runs = await runs_repo.list_for_document(session, document.id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.FAILED
    # Readable, per the phase's done-when bar -- not a bare exception class.
    assert "could not read" in runs[0].error
    assert runs[0].finished_at is not None


async def test_a_key_with_no_object_fails_the_run(
    session: AsyncSession, storage: LocalStorage
) -> None:
    """The download is inside the run, so a key pointing at nothing is recorded
    history rather than an exception with no trace of the attempt.

    This is reachable in production: Postgres is the record of intent and the
    bucket is separate storage, so the row can outlive the object.
    """
    course_id = await _course(session)
    key = storage_key(SEED_USER_ID, "never-uploaded.pdf")

    with pytest.raises(ServiceError, match="missing"):
        await _process(session, course_id, key)

    document = await documents_repo.get_by_storage_key(session, SEED_USER_ID, key)
    assert document.status == DocumentStatus.FAILED

    runs = await runs_repo.list_for_document(session, document.id)
    assert [run.status for run in runs] == [RunStatus.FAILED]
    assert "missing" in runs[0].error


async def test_a_precondition_failure_records_no_run(
    session: AsyncSession, storage: LocalStorage
) -> None:
    """Refusing a re-ingest without `--force` is a complaint about the request,
    not a processing attempt. It must not leave a failed run behind, or the
    history fills with rows that never represented any work."""
    course_id = await _course(session)
    key = await _upload(storage, LECTURE.name, LECTURE.read_bytes())
    result = await _process(session, course_id, key)

    with pytest.raises(ServiceError, match="pass --force"):
        await _process(session, course_id, key)

    runs = await runs_repo.list_for_document(session, result.document_id)
    assert len(runs) == 1


async def test_attempts_increment_and_keep_their_own_errors(
    session: AsyncSession, storage: LocalStorage
) -> None:
    """The reason this table is one row per attempt rather than a counter."""
    course_id = await _course(session)
    key = await _upload(storage, "corrupt-fixture.pdf", CORRUPT_PDF)

    for _ in range(3):
        with pytest.raises(ServiceError):
            await _process(session, course_id, key, force=True)

    document = await documents_repo.get_by_storage_key(session, SEED_USER_ID, key)
    runs = await runs_repo.list_for_document(session, document.id)

    assert [run.attempts for run in runs] == [1, 2, 3]
    assert all(run.status == RunStatus.FAILED for run in runs)
    assert all(run.error for run in runs)


async def test_a_later_attempt_can_succeed_after_failures(
    session: AsyncSession, storage: LocalStorage
) -> None:
    """"Did a retry fix it" is the question this table exists to answer, so the
    failed attempts have to survive the success."""
    course_id = await _course(session)
    key = await _upload(storage, "corrupt-fixture.pdf", CORRUPT_PDF)

    with pytest.raises(ServiceError):
        await _process(session, course_id, key)

    # Same key, so the same document -- now with readable bytes behind it, which
    # is what re-uploading a corrected file does.
    await storage.upload(key, LECTURE.read_bytes())
    await _process(session, course_id, key, force=True)

    document = await documents_repo.get_by_storage_key(session, SEED_USER_ID, key)
    runs = await runs_repo.list_for_document(session, document.id)

    assert [run.status for run in runs] == [RunStatus.FAILED, RunStatus.SUCCEEDED]
    assert runs[0].error is not None
    assert runs[1].error is None
