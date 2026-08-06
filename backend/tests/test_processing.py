"""The `processing_runs` lifecycle.

Every test here runs with `embed=False`, which keeps the suite free of an OpenAI
key and of network calls. That is not a workaround: the run lifecycle is exactly
the part that has to be right when the expensive step is *not* what failed.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.paths import REPO_ROOT
from app.models import DocumentStatus, RunStatus
from app.models.user import SEED_USER_ID
from app.repositories import documents as documents_repo
from app.repositories import processing_runs as runs_repo
from app.services.errors import ServiceError
from app.services.ingestion import create_course
from app.services.processing import process_document

LECTURE = REPO_ROOT / "test-data" / "Depth-First_Search_Lecture.pdf"


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


async def _process(session: AsyncSession, course_id: uuid.UUID, path, *, force: bool = False):
    return await process_document(
        session,
        user_id=SEED_USER_ID,
        course_id=course_id,
        path=path,
        kind="lecture",
        title=path.stem,
        force=force,
        embed=False,
    )


async def test_a_successful_run_is_recorded(session: AsyncSession) -> None:
    course_id = await _course(session)
    result = await _process(session, course_id, LECTURE)

    runs = await runs_repo.list_for_document(session, result.document_id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.SUCCEEDED
    assert runs[0].attempts == 1
    assert runs[0].error is None
    assert runs[0].started_at is not None
    assert runs[0].finished_at is not None


async def test_embeddings_skipped_leaves_the_document_short_of_ready(
    session: AsyncSession,
) -> None:
    """A document with chunks but no vectors cannot be searched, so it is not
    `ready`. Phase 1 set this rule; the run still counts as succeeded, because
    the attempt did what it was asked to do."""
    course_id = await _course(session)
    result = await _process(session, course_id, LECTURE)

    document = await documents_repo.get(session, result.document_id, SEED_USER_ID)
    assert document.status == DocumentStatus.PROCESSING
    assert result.embedding is None


async def test_a_corrupt_pdf_fails_the_run_and_the_document(
    session: AsyncSession, tmp_path
) -> None:
    course_id = await _course(session)
    # Inside the repo, because `storage_path` is repo-relative and
    # `resolve_document` refuses anything outside it.
    corrupt = REPO_ROOT / "test-data" / "corrupt-fixture.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nnot actually a pdf\n")

    try:
        with pytest.raises(ServiceError, match="could not read"):
            await _process(session, course_id, corrupt)

        document = await documents_repo.get_by_storage_path(
            session, SEED_USER_ID, "test-data/corrupt-fixture.pdf"
        )
        assert document.status == DocumentStatus.FAILED

        runs = await runs_repo.list_for_document(session, document.id)
        assert len(runs) == 1
        assert runs[0].status == RunStatus.FAILED
        # Readable, per the phase's done-when bar -- not a bare exception class.
        assert "could not read" in runs[0].error
        assert runs[0].finished_at is not None
    finally:
        corrupt.unlink()


async def test_a_precondition_failure_records_no_run(session: AsyncSession) -> None:
    """Refusing a re-ingest without `--force` is a complaint about the request,
    not a processing attempt. It must not leave a failed run behind, or the
    history fills with rows that never represented any work."""
    course_id = await _course(session)
    result = await _process(session, course_id, LECTURE)

    with pytest.raises(ServiceError, match="pass --force"):
        await _process(session, course_id, LECTURE)

    runs = await runs_repo.list_for_document(session, result.document_id)
    assert len(runs) == 1


async def test_attempts_increment_and_keep_their_own_errors(
    session: AsyncSession, tmp_path
) -> None:
    """The reason this table is one row per attempt rather than a counter."""
    course_id = await _course(session)
    corrupt = REPO_ROOT / "test-data" / "corrupt-fixture.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nnot actually a pdf\n")

    try:
        for _ in range(3):
            with pytest.raises(ServiceError):
                await _process(session, course_id, corrupt, force=True)

        document = await documents_repo.get_by_storage_path(
            session, SEED_USER_ID, "test-data/corrupt-fixture.pdf"
        )
        runs = await runs_repo.list_for_document(session, document.id)

        assert [run.attempts for run in runs] == [1, 2, 3]
        assert all(run.status == RunStatus.FAILED for run in runs)
        assert all(run.error for run in runs)
    finally:
        corrupt.unlink()


async def test_a_later_attempt_can_succeed_after_failures(
    session: AsyncSession,
) -> None:
    """"Did a retry fix it" is the question this table exists to answer, so the
    failed attempts have to survive the success."""
    course_id = await _course(session)
    fixture = REPO_ROOT / "test-data" / "corrupt-fixture.pdf"
    fixture.write_bytes(b"%PDF-1.4\nnot actually a pdf\n")

    try:
        with pytest.raises(ServiceError):
            await _process(session, course_id, fixture)

        # Same path, so the same document -- now readable.
        fixture.write_bytes(LECTURE.read_bytes())
        await _process(session, course_id, fixture, force=True)

        document = await documents_repo.get_by_storage_path(
            session, SEED_USER_ID, "test-data/corrupt-fixture.pdf"
        )
        runs = await runs_repo.list_for_document(session, document.id)

        assert [run.status for run in runs] == [RunStatus.FAILED, RunStatus.SUCCEEDED]
        assert runs[0].error is not None
        assert runs[1].error is None
    finally:
        fixture.unlink()
