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
from app.services.dating import date_course_from_filenames, redate_document
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

    assert by_name(outcomes, "lec1.pdf").occurred_on == STARTS_ON
    assert by_name(outcomes, "lec11.pdf").occurred_on == ENDS_ON
    assert by_name(outcomes, "lec6.pdf").occurred_on == date(2020, 3, 24)
    assert all(o.source is OccurredAtSource.INFERRED_FILENAME for o in outcomes)


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

    assert by_name(outcomes, "r01.pdf").occurred_on == STARTS_ON
    assert by_name(outcomes, "r03.pdf").occurred_on == ENDS_ON
    assert by_name(outcomes, "lec20.pdf").occurred_on == ENDS_ON


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
async def test_an_inferred_date_is_left_alone_without_overwrite(
    session: AsyncSession, course: Course
) -> None:
    await add(session, course, "lec1.pdf")
    await add(session, course, "lec9.pdf")
    await date_course_from_filenames(session, user_id=SEED_USER_ID, course_id=course.id)

    again = await date_course_from_filenames(
        session, user_id=SEED_USER_ID, course_id=course.id
    )

    assert {outcome.reason for outcome in again} == {"already dated"}


@pytest.mark.asyncio
async def test_an_unknown_course_is_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await date_course_from_filenames(
            session, user_id=SEED_USER_ID, course_id=uuid.uuid4()
        )
