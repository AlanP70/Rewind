"""`date_course_from_filenames` against a real database.

The parsing itself is pinned in `test_filename_dates.py`. What is tested here is
everything the parser cannot see on its own: which documents are eligible, that
the two kinds of ordinal do not contaminate each other, and that a date the
funnel refuses comes back as a reported outcome rather than an exception.
"""

import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, OccurredAtSource
from app.models.user import SEED_USER_ID
from app.repositories import documents as documents_repo
from app.services.dating import (
    DateCandidate,
    date_course_from_filenames,
    redate_document,
)
from app.services.errors import NotFoundError
from app.services.ingestion import create_course

STARTS_ON = date(2020, 2, 3)
ENDS_ON = date(2020, 5, 12)


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


async def add(session: AsyncSession, course: Course, filename: str) -> uuid.UUID:
    """A document whose storage key ends in `filename`, which is what gets parsed."""
    document = await documents_repo.create(
        session,
        user_id=SEED_USER_ID,
        course_id=course.id,
        kind="lecture",
        title="whatever the uploader typed",
        storage_key=f"{SEED_USER_ID}/{filename}",
    )
    await session.commit()
    return document.id


def by_name(outcomes: list, filename: str):
    return next(outcome for outcome in outcomes if outcome.filename == filename)


@pytest.mark.asyncio
async def test_ordinals_are_spread_across_the_term(
    session: AsyncSession, course: Course
) -> None:
    for name in ("lec1.pdf", "lec6.pdf", "lec11.pdf"):
        await add(session, course, name)

    outcomes = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    assert by_name(outcomes, "lec1.pdf").candidates[0].occurred_on == STARTS_ON
    assert by_name(outcomes, "lec11.pdf").candidates[0].occurred_on == ENDS_ON
    assert by_name(outcomes, "lec6.pdf").candidates[0].occurred_on == date(2020, 3, 24)


@pytest.mark.asyncio
async def test_an_interpolated_date_is_offered_and_never_stored(
    session: AsyncSession, course: Course
) -> None:
    """The decision this slice closed on, in a form that can fail.

    Interpolated dates fail by weeks, not days -- `lec11` here is the highest
    lecture uploaded, so it lands on the last day of term regardless of where
    lecture 11 of a real 20-lecture course actually fell. Until that error is
    measured against real lecture dates, the candidate is offered and the
    document stays honestly undated.
    """
    document_id = await add(session, course, "lec1.pdf")
    await add(session, course, "lec11.pdf")

    outcomes = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    offered = by_name(outcomes, "lec1.pdf")
    assert offered.candidates[0] == DateCandidate(
        source=OccurredAtSource.INFERRED_FILENAME, occurred_on=STARTS_ON
    )
    assert offered.occurred_on is None
    assert offered.source is None

    stored = await documents_repo.get(session, document_id, SEED_USER_ID)
    assert stored is not None
    assert stored.occurred_at is None
    assert stored.occurred_at_source is None


@pytest.mark.asyncio
async def test_a_stated_date_beats_an_ordinal_in_the_same_name(
    session: AsyncSession, course: Course
) -> None:
    """`2020-02-11-lec3.pdf` states a date. Reading it beats computing one."""
    await add(session, course, "2020-02-11-lec3.pdf")
    await add(session, course, "lec9.pdf")

    outcomes = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    stated = by_name(outcomes, "2020-02-11-lec3.pdf")
    assert stated.occurred_on == date(2020, 2, 11)
    assert stated.source is OccurredAtSource.FILENAME_DATE


@pytest.mark.asyncio
async def test_two_sequences_do_not_share_a_range(
    session: AsyncSession, course: Course
) -> None:
    """Twenty lectures and three recitations are two sequences, not one.

    Pooled, recitation 3 would land near the start of term alongside lecture 3.
    Grouped by kind, it lands at the end of its own sequence -- which is where
    the third of three recitations actually falls.
    """
    for name in ("lec1.pdf", "lec20.pdf", "r01.pdf", "r03.pdf"):
        await add(session, course, name)

    outcomes = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    assert by_name(outcomes, "r01.pdf").candidates[0].occurred_on == STARTS_ON
    assert by_name(outcomes, "r03.pdf").candidates[0].occurred_on == ENDS_ON
    assert by_name(outcomes, "lec20.pdf").candidates[0].occurred_on == ENDS_ON


@pytest.mark.asyncio
async def test_a_lone_ordinal_is_reported_undated(
    session: AsyncSession, course: Course
) -> None:
    await add(session, course, "lecture-07.pdf")

    outcomes = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    assert outcomes[0].occurred_on is None
    assert "no range" in outcomes[0].reason


@pytest.mark.asyncio
async def test_an_unrecognisable_filename_is_reported_undated(
    session: AsyncSession, course: Course
) -> None:
    """The phase's headline behaviour: surfaced, never silently defaulted."""
    document_id = await add(session, course, "scan.pdf")

    outcomes = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    assert outcomes[0].occurred_on is None
    assert outcomes[0].reason
    stored = await documents_repo.get(session, document_id, SEED_USER_ID)
    assert stored is not None and stored.occurred_at is None


@pytest.mark.asyncio
async def test_a_stated_date_outside_the_term_is_refused_not_raised(
    session: AsyncSession, course: Course
) -> None:
    """The funnel refuses it; the batch reports it and keeps going.

    A filename stating a date in the wrong year is a real thing that happens, and
    it must not take the other nineteen documents down with it.
    """
    await add(session, course, "2019-11-04-lecture.pdf")
    await add(session, course, "2020-03-02-lecture.pdf")

    outcomes = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    refused = by_name(outcomes, "2019-11-04-lecture.pdf")
    assert refused.occurred_on is None
    assert "outside" in refused.reason
    assert by_name(outcomes, "2020-03-02-lecture.pdf").occurred_on == date(2020, 3, 2)


@pytest.mark.asyncio
async def test_a_hand_set_date_is_never_overwritten(
    session: AsyncSession, course: Course
) -> None:
    """Not even with `overwrite=True` -- the one rule this function must not break.

    A person who typed a date in outranks every heuristic here. Replacing their
    answer with an interpolation is the worst thing this code could do, so the
    flag that exists to re-run inference deliberately cannot reach it.
    """
    document_id = await add(session, course, "lec1.pdf")
    await add(session, course, "lec9.pdf")
    await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document_id,
        occurred_on=date(2020, 4, 1),
        source=OccurredAtSource.MANUAL,
    )

    outcomes = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id, overwrite=True
    )

    assert by_name(outcomes, "lec1.pdf").reason == "dated by hand"
    stored = await documents_repo.get(session, document_id, SEED_USER_ID)
    assert stored is not None
    assert stored.occurred_at == datetime(2020, 4, 1, tzinfo=UTC)
    assert stored.occurred_at_source == OccurredAtSource.MANUAL


@pytest.mark.asyncio
async def test_a_stored_date_is_left_alone_without_overwrite(
    session: AsyncSession, course: Course
) -> None:
    await add(session, course, "2020-03-02-lecture.pdf")
    await date_course_from_filenames(session, user_id=SEED_USER_ID, course_id=course.id)

    again = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    assert {outcome.reason for outcome in again} == {"already dated"}


@pytest.mark.asyncio
async def test_a_suggestion_is_recomputed_every_run(
    session: AsyncSession, course: Course
) -> None:
    """Suggestions are derived, never cached -- and uploading changes them.

    The interpolation range comes from the ordinals actually present, so lecture
    9 sits at the end of term until lecture 20 arrives and pushes it to the
    middle. A stored candidate would be stale the moment the next file lands;
    recomputing is correct by construction.
    """
    await add(session, course, "lec1.pdf")
    await add(session, course, "lec9.pdf")
    first = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )
    assert by_name(first, "lec9.pdf").candidates[0].occurred_on == ENDS_ON

    await add(session, course, "lec20.pdf")
    second = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    assert by_name(second, "lec9.pdf").candidates[0].occurred_on == date(2020, 3, 16)
    assert by_name(second, "lec20.pdf").candidates[0].occurred_on == ENDS_ON


@pytest.mark.asyncio
async def test_an_unknown_course_is_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await date_course_from_filenames(
            session, user_id=SEED_USER_ID, course_id=uuid.uuid4()
        )
