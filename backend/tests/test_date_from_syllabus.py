"""`date_course_from_syllabus` — the ordinal join and the disagreement signal.

The syllabus arrives here already structured, as `ScheduleEntry` rows. Turning a
PDF into those is layout matching that needs real syllabi to build against and
lands separately; everything below is independent of how the schedule was
obtained, which is why it is testable now.
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
    ScheduleEntry,
    date_course_from_syllabus,
    redate_document,
)
from app.services.errors import NotFoundError, ServiceError
from app.services.ingestion import create_course

STARTS_ON = date(2020, 2, 3)
ENDS_ON = date(2020, 5, 12)

# A real timetable, in the sense that matters: unevenly spaced. Lecture 3 is not
# where interpolation would put it, which is the whole reason this path exists.
SCHEDULE = [
    ScheduleEntry(kind="lecture", ordinal=1, occurred_on=date(2020, 2, 4)),
    ScheduleEntry(kind="lecture", ordinal=2, occurred_on=date(2020, 2, 6)),
    ScheduleEntry(kind="lecture", ordinal=3, occurred_on=date(2020, 2, 11)),
    ScheduleEntry(kind="recitation", ordinal=1, occurred_on=date(2020, 2, 7)),
]


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


async def run(session: AsyncSession, course: Course, **kwargs) -> list:
    return await date_course_from_syllabus(
        session,
        user_id=SEED_USER_ID,
        course_id=course.id,
        schedule=kwargs.pop("schedule", SCHEDULE),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_the_syllabus_date_is_stored_against_the_matching_ordinal(
    session: AsyncSession, course: Course
) -> None:
    """The point of the slice: an ordinal that could only be guessed is now known.

    `lec3.pdf` interpolated across a 1..3 range would land on the last day of
    term. The syllabus says February 11th, and that is what gets written.
    """
    document_id = await add(session, course, "lec3.pdf")
    await add(session, course, "lec1.pdf")

    outcomes = await run(session, course)

    matched = by_name(outcomes, "lec3.pdf")
    assert matched.occurred_on == date(2020, 2, 11)
    assert matched.source is OccurredAtSource.PARSED_SYLLABUS

    stored = await documents_repo.get(session, document_id, SEED_USER_ID)
    assert stored is not None
    assert stored.occurred_at == datetime(2020, 2, 11, tzinfo=UTC)


@pytest.mark.asyncio
async def test_kinds_are_matched_independently(
    session: AsyncSession, course: Course
) -> None:
    """Recitation 1 is not lecture 1, and they fall on different days."""
    await add(session, course, "lec1.pdf")
    await add(session, course, "r01.pdf")

    outcomes = await run(session, course)

    assert by_name(outcomes, "lec1.pdf").occurred_on == date(2020, 2, 4)
    assert by_name(outcomes, "r01.pdf").occurred_on == date(2020, 2, 7)


@pytest.mark.asyncio
async def test_a_disagreement_stores_neither_and_offers_both(
    session: AsyncSession, course: Course
) -> None:
    """The signal this slice exists to produce.

    The filename says the 13th, the syllabus says the 11th. Syllabi are published
    in advance and classes get moved, so the schedule is not automatically right;
    a student's filename is not automatically right either. Storing either one
    silently would be the phase's central failure, and it would be worse than an
    undated document because the conflict is evidence that one of these two
    sources is unreliable for the whole course.
    """
    document_id = await add(session, course, "2020-02-13-lec3.pdf")

    outcomes = await run(session, course)

    conflict = by_name(outcomes, "2020-02-13-lec3.pdf")
    assert conflict.occurred_on is None
    assert conflict.candidates == (
        DateCandidate(
            source=OccurredAtSource.PARSED_SYLLABUS, occurred_on=date(2020, 2, 11)
        ),
        DateCandidate(
            source=OccurredAtSource.FILENAME_DATE, occurred_on=date(2020, 2, 13)
        ),
    )

    stored = await documents_repo.get(session, document_id, SEED_USER_ID)
    assert stored is not None
    assert stored.occurred_at is None


@pytest.mark.asyncio
async def test_agreement_is_not_a_conflict(
    session: AsyncSession, course: Course
) -> None:
    """Two sources saying the same thing is the good case, not a tie to break."""
    await add(session, course, "2020-02-11-lec3.pdf")

    outcomes = await run(session, course)

    agreed = by_name(outcomes, "2020-02-11-lec3.pdf")
    assert agreed.occurred_on == date(2020, 2, 11)
    assert agreed.source is OccurredAtSource.PARSED_SYLLABUS
    assert agreed.candidates == ()


@pytest.mark.asyncio
async def test_a_filename_date_is_used_where_the_syllabus_is_silent(
    session: AsyncSession, course: Course
) -> None:
    """A guest lecture with no syllabus row still has a date written on it."""
    await add(session, course, "2020-04-07_guest.pdf")

    outcomes = await run(session, course)

    assert outcomes[0].occurred_on == date(2020, 4, 7)
    assert outcomes[0].source is OccurredAtSource.FILENAME_DATE


@pytest.mark.asyncio
async def test_an_ordinal_the_syllabus_does_not_list_stays_undated(
    session: AsyncSession, course: Course
) -> None:
    """No interpolation fallback, deliberately.

    Lecture 9 is missing from this schedule. Filling the gap by spreading it
    across the term is exactly the unmeasured guess slice 2 stopped storing, and
    a syllabus being present does not make that guess any better.
    """
    await add(session, course, "lec9.pdf")

    outcomes = await run(session, course)

    assert outcomes[0].occurred_on is None
    assert outcomes[0].candidates == ()
    assert "no lecture 9" in outcomes[0].reason


WEEKLY = [
    ScheduleEntry(kind="week", ordinal=1, occurred_on=date(2020, 2, 3)),
    ScheduleEntry(kind="week", ordinal=2, occurred_on=date(2020, 2, 10)),
    ScheduleEntry(kind="week", ordinal=3, occurred_on=date(2020, 2, 17)),
]


@pytest.mark.asyncio
async def test_a_weekly_schedule_does_not_date_a_lecture(
    session: AsyncSession, course: Course
) -> None:
    """Waterloo's schedule is headed `Week of`. Its ordinals are not lectures.

    `(3) Sep 20` means the week beginning the 20th, and a course with two lectures
    a week has lectures 5 and 6 inside week 3. Converting between them needs a
    lectures-per-week figure that appears in neither the syllabus nor the
    filenames. Deriving it from the upload assumes the student uploaded every
    lecture, which is the assumption slice 2 measured going wrong by weeks.

    The decisive objection is the column, not the arithmetic: a date reached that
    way would be stored as `parsed_syllabus` — *the syllabus stated this* — when
    it stated nothing of the kind. That is a false claim carrying the strongest
    provenance the enum has.
    """
    await add(session, course, "lecture-02.pdf")

    outcomes = await run(session, course, schedule=WEEKLY)

    assert outcomes[0].occurred_on is None
    assert outcomes[0].candidates == ()


@pytest.mark.asyncio
async def test_the_granularity_mismatch_is_named_not_reported_as_a_missing_row(
    session: AsyncSession, course: Course
) -> None:
    """`the syllabus has no lecture 2` would send someone hunting for a row.

    There is no missing row. The two sides count different things, and only the
    reason string can say so — the outcome is undated either way, which is exactly
    why the wrong wording here would go unnoticed.
    """
    await add(session, course, "lecture-02.pdf")

    outcomes = await run(session, course, schedule=WEEKLY)

    assert "numbers weeks" in outcomes[0].reason
    assert "several lectures" in outcomes[0].reason


@pytest.mark.asyncio
async def test_a_weekly_schedule_still_dates_weekly_material(
    session: AsyncSession, course: Course
) -> None:
    """The weekly schedule is not useless — it dates what it actually numbers.

    A course distributing `week-03-notes.pdf` joins exactly, and the date is one
    the syllabus stated. Nothing about the mismatch above is a reason to discard
    a schedule; it is a reason not to reinterpret it.
    """
    await add(session, course, "week-03-notes.pdf")

    outcomes = await run(session, course, schedule=WEEKLY)

    assert outcomes[0].occurred_on == date(2020, 2, 17)
    assert outcomes[0].source == OccurredAtSource.PARSED_SYLLABUS


@pytest.mark.asyncio
async def test_a_schedule_date_outside_the_term_is_refused_not_raised(
    session: AsyncSession, course: Course
) -> None:
    await add(session, course, "lec1.pdf")

    outcomes = await run(
        session,
        course,
        schedule=[
            ScheduleEntry(kind="lecture", ordinal=1, occurred_on=date(2019, 9, 3))
        ],
    )

    assert outcomes[0].occurred_on is None
    assert "outside" in outcomes[0].reason


@pytest.mark.asyncio
async def test_a_schedule_that_dates_one_session_twice_is_rejected(
    session: AsyncSession, course: Course
) -> None:
    """Whichever row won would depend on iteration order, which is not a decision."""
    await add(session, course, "lec1.pdf")

    with pytest.raises(ServiceError, match="two dates"):
        await run(
            session,
            course,
            schedule=[
                ScheduleEntry(kind="lecture", ordinal=1, occurred_on=date(2020, 2, 4)),
                ScheduleEntry(kind="lecture", ordinal=1, occurred_on=date(2020, 2, 6)),
            ],
        )


@pytest.mark.asyncio
async def test_a_repeated_identical_row_is_fine(
    session: AsyncSession, course: Course
) -> None:
    """Only a *contradiction* is an error. A duplicate row says nothing new."""
    await add(session, course, "lec1.pdf")

    outcomes = await run(
        session,
        course,
        schedule=[
            ScheduleEntry(kind="lecture", ordinal=1, occurred_on=date(2020, 2, 4)),
            ScheduleEntry(kind="lecture", ordinal=1, occurred_on=date(2020, 2, 4)),
        ],
    )

    assert outcomes[0].occurred_on == date(2020, 2, 4)


@pytest.mark.asyncio
async def test_a_hand_set_date_is_never_overwritten(
    session: AsyncSession, course: Course
) -> None:
    """Same rule as filename dating: a person outranks the syllabus too."""
    document_id = await add(session, course, "lec1.pdf")
    await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document_id,
        occurred_on=date(2020, 3, 2),
        source=OccurredAtSource.MANUAL,
    )

    outcomes = await run(session, course, overwrite=True)

    assert outcomes[0].reason == "dated by hand"
    stored = await documents_repo.get(session, document_id, SEED_USER_ID)
    assert stored is not None
    assert stored.occurred_at == datetime(2020, 3, 2, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_syllabus_date_replaces_an_inferred_one_with_overwrite(
    session: AsyncSession, course: Course
) -> None:
    """The upgrade path. A guess becomes a fact, and the source moves with it."""
    document_id = await add(session, course, "lec3.pdf")
    await redate_document(
        session,
        user_id=SEED_USER_ID,
        document_id=document_id,
        occurred_on=date(2020, 5, 12),
        source=OccurredAtSource.INFERRED_FILENAME,
    )

    outcomes = await run(session, course, overwrite=True)

    assert outcomes[0].occurred_on == date(2020, 2, 11)
    stored = await documents_repo.get(session, document_id, SEED_USER_ID)
    assert stored is not None
    assert stored.occurred_at == datetime(2020, 2, 11, tzinfo=UTC)
    assert stored.occurred_at_source == OccurredAtSource.PARSED_SYLLABUS


@pytest.mark.asyncio
async def test_an_unknown_course_is_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await date_course_from_syllabus(
            session,
            user_id=SEED_USER_ID,
            course_id=uuid.uuid4(),
            schedule=SCHEDULE,
        )
