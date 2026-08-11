"""`redate_document` and the manual override route.

Against a real database, because the two things worth pinning here are both
enforced by the schema as much as by Python: the widened
`ck_documents_occurred_at_source` from migration 0005, and
`ck_documents_occurred_at_has_source` binding a date to its provenance.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.main import app
from app.models import Course, Document, OccurredAtSource
from app.models.user import SEED_USER_ID
from app.repositories import documents as documents_repo
from app.services.dating import redate_document
from app.services.errors import NotFoundError, ServiceError
from app.services.ingestion import create_course

STARTS_ON = date(2024, 9, 1)
ENDS_ON = date(2024, 12, 15)


@pytest_asyncio.fixture
async def course(session: AsyncSession) -> Course:
    created = await create_course(
        session,
        user_id=SEED_USER_ID,
        name="Algorithms",
        starts_on=STARTS_ON,
        ends_on=ENDS_ON,
    )
    await session.commit()
    return created


@pytest_asyncio.fixture
async def document(session: AsyncSession, course: Course) -> Document:
    """An undated document, which is what every document is before this phase."""
    created = await documents_repo.create(
        session,
        user_id=SEED_USER_ID,
        course_id=course.id,
        kind="lecture",
        title="Lecture 3",
        storage_key=f"{SEED_USER_ID}/lecture-3.pdf",
    )
    await session.commit()
    assert created.occurred_at is None
    return created


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", list(OccurredAtSource))
async def test_every_source_round_trips(
    session: AsyncSession, document: Document, source: OccurredAtSource
) -> None:
    """All four values survive the check constraint.

    Parametrised over the enum rather than a hardcoded list, so adding a fifth
    source without migrating the constraint fails here instead of in production
    the first time that source is used.
    """
    result = await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document.id,
        occurred_on=date(2024, 10, 3),
        source=source,
    )

    assert result.document.occurred_at_source == source
    assert result.outside_term is False


@pytest.mark.asyncio
async def test_a_date_is_stored_at_midnight_utc(
    session: AsyncSession, document: Document
) -> None:
    """The column is TIMESTAMPTZ and every source is day-granular.

    Pinned because the alternative -- whatever local midnight the server happens
    to sit in -- makes ordering depend on where the process runs, and Phase 4
    orders the entire product by this column.
    """
    result = await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document.id,
        occurred_on=date(2024, 10, 3),
        source=OccurredAtSource.PARSED_SYLLABUS,
    )

    assert result.document.occurred_at == datetime(2024, 10, 3, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("occurred_on", [STARTS_ON, ENDS_ON])
async def test_term_bounds_are_inclusive(
    session: AsyncSession, document: Document, occurred_on: date
) -> None:
    """A course's first and last day are inside its term.

    An off-by-one here rejects the first lecture of every syllabus, which is both
    the most likely date to be correct and the one a reader would blame on the
    parser rather than on the bounds check.
    """
    result = await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document.id,
        occurred_on=occurred_on,
        source=OccurredAtSource.PARSED_SYLLABUS,
    )

    assert result.outside_term is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        OccurredAtSource.PARSED_SYLLABUS,
        OccurredAtSource.FILENAME_DATE,
        OccurredAtSource.INFERRED_FILENAME,
    ],
)
async def test_an_inferred_date_outside_the_term_is_refused(
    session: AsyncSession, document: Document, source: OccurredAtSource
) -> None:
    """A wrong date is worse than no date -- the phase's whole premise.

    The document must come back still undated, not undated-looking: a refusal
    that half-wrote the row would leave a date with no source, which the check
    constraint would reject anyway, and the message would name the wrong problem.
    """
    with pytest.raises(ServiceError, match="outside"):
        await redate_document(
            session,
            user_id=SEED_USER_ID,
            document_id=document.id,
            occurred_on=date(2025, 3, 1),
            source=source,
        )

    stored = await documents_repo.get(session, document.id, SEED_USER_ID)
    assert stored is not None
    assert stored.occurred_at is None
    assert stored.occurred_at_source is None


@pytest.mark.asyncio
async def test_a_manual_date_outside_the_term_is_kept_and_flagged(
    session: AsyncSession, document: Document, caplog: pytest.LogCaptureFixture
) -> None:
    """The user is the authority on when their own lecture happened.

    Refusing would leave a document nobody can fix. Accepting silently would hide
    a course whose term bounds are wrong, which is the more likely cause of a
    whole set of these -- hence the flag and the log line.
    """
    caplog.set_level(logging.WARNING, logger="app")

    result = await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document.id,
        occurred_on=date(2025, 3, 1),
        source=OccurredAtSource.MANUAL,
    )

    assert result.outside_term is True
    assert result.document.occurred_at == datetime(2025, 3, 1, tzinfo=UTC)
    assert result.document.occurred_at_source == OccurredAtSource.MANUAL
    assert "outside" in caplog.text


@pytest.mark.asyncio
async def test_redating_replaces_both_the_date_and_its_source(
    session: AsyncSession, document: Document
) -> None:
    """A correction has to move the provenance with the date.

    A manual fix that left `inferred_filename` behind would show the user their
    own answer labelled as a guess -- and the timeline would keep treating it as
    one.
    """
    await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document.id,
        occurred_on=date(2024, 10, 3),
        source=OccurredAtSource.INFERRED_FILENAME,
    )
    result = await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document.id,
        occurred_on=date(2024, 10, 4),
        source=OccurredAtSource.MANUAL,
    )

    assert result.document.occurred_at == datetime(2024, 10, 4, tzinfo=UTC)
    assert result.document.occurred_at_source == OccurredAtSource.MANUAL


@pytest.mark.asyncio
async def test_an_unknown_document_is_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await redate_document(
            session,
            user_id=SEED_USER_ID,
            document_id=uuid.uuid4(),
            occurred_on=date(2024, 10, 3),
            source=OccurredAtSource.MANUAL,
        )


@pytest.mark.asyncio
async def test_patch_sets_the_date_and_reports_the_term(
    client: AsyncClient, document: Document
) -> None:
    response = await client.patch(
        f"/documents/{document.id}/date", json={"occurred_on": "2024-10-03"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["occurred_at"] == "2024-10-03T00:00:00Z"
    assert body["occurred_at_source"] == "manual"
    assert body["starts_on"] == "2024-09-01"
    assert body["ends_on"] == "2024-12-15"
    assert body["outside_term"] is False


@pytest.mark.asyncio
async def test_patch_accepts_an_out_of_term_date_with_200(
    client: AsyncClient, document: Document
) -> None:
    """200, not 4xx. The date was stored; `outside_term` is the caveat, not a
    failure the client should retry."""
    response = await client.patch(
        f"/documents/{document.id}/date", json={"occurred_on": "2025-03-01"}
    )

    assert response.status_code == 200
    assert response.json()["outside_term"] is True


@pytest.mark.asyncio
async def test_patch_on_an_unknown_document_is_404(client: AsyncClient) -> None:
    response = await client.patch(
        f"/documents/{uuid.uuid4()}/date", json={"occurred_on": "2024-10-03"}
    )

    assert response.status_code == 404
