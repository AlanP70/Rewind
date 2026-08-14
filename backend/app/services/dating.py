"""Deciding when a document happened, and recording how we decided.

Every path that dates a document -- syllabus parsing, filename inference, a user
correcting one by hand -- ends here, at `redate_document`. Nothing else writes
`documents.occurred_at`.

That funnel exists for a write that does not exist yet. Phase 5 denormalises
`occurred_at` onto `concept_mentions` so the timeline query is one index scan
(ARCHITECTURE, `concept_mentions`), which means two rows of truth for one fact
and a rule that they only ever change in the same transaction. No constraint can
express that -- a foreign key pins an identity, not a value expected to be
updated -- so the obligation has to hang on a single function instead. Building
that function later, once three callers each own an UPDATE, is a refactor nobody
schedules.

It is not only a placeholder, though, and that matters: the term-bounds check
below is real work this phase needs, and it is the reason this is worth calling
today rather than a wrapper someone reasonably inlines.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, OccurredAtSource
from app.repositories import courses as courses_repo
from app.repositories import documents as documents_repo
from app.services import filename_dates
from app.services.errors import NotFoundError, ServiceError
from app.services.extraction import extract_pages
from app.services.syllabus_schedule import ParsedSchedule, ScheduleEntry, parse_schedule

logger = logging.getLogger("app")


@dataclass(frozen=True)
class RedateResult:
    """What happened, including the part the caller must not ignore."""

    document: Document
    starts_on: date
    ends_on: date

    # True only for a manual date the user placed outside its course's term. The
    # date is stored anyway -- see `redate_document` -- and this is how the API
    # and the UI say so instead of accepting it silently.
    outside_term: bool


@dataclass(frozen=True)
class DateCandidate:
    """A date that was worked out but deliberately not written, and where it came from.

    Carried as data rather than described in `reason`, because the UI has to
    offer it as a one-click answer. A conflict explained only in prose is not a
    signal anything can act on.
    """

    source: OccurredAtSource
    occurred_on: date


@dataclass(frozen=True)
class DatingOutcome:
    """What the dater decided about one document, and why.

    Three shapes, and the middle one is the point:

      `occurred_on` set   -- a date was written, and `source` says how.
      `candidates` set    -- date(s) were worked out and deliberately *not*
                             written. The document is still undated. One
                             candidate is an offer; two is a disagreement
                             between sources that a person has to settle.
      neither set         -- nothing was found. `reason` says what was missing.

    `reason` is filled in exactly when `occurred_on` is `None`, which covers both
    of the latter two. An undated document is a result this phase reports rather
    than an error it swallows, so the reason travels with it instead of being
    logged and lost.
    """

    document_id: uuid.UUID
    filename: str
    occurred_on: date | None = None
    source: OccurredAtSource | None = None
    candidates: tuple[DateCandidate, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class DatePlan:
    """What a filename dating run *would* do to one document, before it does it.

    The same three shapes as `DatingOutcome` -- a date to write, candidates to
    offer, or a reason for neither -- over the document rather than its id,
    because both callers need more of the row than an id.

    It is a separate type from `DatingOutcome` on purpose. `occurred_on` on an
    outcome means a date **was** written; here it means one **would** be. Reusing
    one type and letting the caller decide which reading applies is how a read
    request ends up displaying a date nothing stored.

    `reason` says what was found, and unlike `DatingOutcome`'s it is set on the
    write branch too -- from a reader's side that document is still undated and
    still needs a sentence explaining the empty cell.
    """

    document: Document
    occurred_on: date | None = None
    source: OccurredAtSource | None = None
    candidates: tuple[DateCandidate, ...] = ()
    reason: str = ""

    @property
    def filename(self) -> str:
        """The name the file was uploaded under -- see `_filename`."""
        return _filename(self.document)

    @property
    def offers(self) -> tuple[DateCandidate, ...]:
        """Every date on this plan that is not in the database.

        **A planned write is an offer too.** `date_course_from_filenames` would
        store `occurred_on` itself, but a reader has not, so to anything that only
        reads, the two are the same thing -- a date worked out and not stored --
        and the only difference is who has to click. Collapsing them here is what
        lets the list route be a mapping with no decisions in it.

        The order is fixed by construction and never sorted. `source` is
        provenance, not a score, so ordering by it would put a recommendation in
        front of a person who is being asked to decide.
        """
        if self.occurred_on is None or self.source is None:
            return self.candidates
        return (
            DateCandidate(source=self.source, occurred_on=self.occurred_on),
            *self.candidates,
        )


@dataclass(frozen=True)
class CourseDatePlan:
    """Every document in one course, and the term its dates are judged against.

    The bounds ride along because the caller that wants the plans -- a UI listing
    undated documents so a person can fill them in -- needs the range that date
    input is checked against, and it comes from the same row the planner already
    had to fetch.
    """

    starts_on: date
    ends_on: date
    documents: tuple[DatePlan, ...]


async def plan_dates_from_filenames(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    overwrite: bool = False,
) -> CourseDatePlan:
    """Work out what every document's filename says. Writes nothing.

    **A separate function rather than a `dry_run=True` on the writer.** This whole
    module exists to funnel writes through one place; a flag that switches writing
    off is a second, invisible mode of the funnel, and getting it wrong means a
    GET quietly dates a course. The seam is enforced by there being no `await
    redate_document` below this line rather than by a parameter nobody re-reads.

    Every document in the course comes back, in upload order, including the ones
    nothing can be done with -- an already-dated document gets an empty plan and
    the reason it was skipped. Returning only the actionable ones would make the
    caller re-fetch the rest to render a list.

    **Only a date the filename states becomes a write. An interpolated one is
    offered.**
    A `filename_date` is testimony -- someone wrote `2020-02-11` in the name, and
    it is wrong only if they were wrong. An `inferred_filename` date is arithmetic
    on top of testimony about something else, its accuracy has never been
    measured against real lecture dates, and it fails by *weeks* rather than days
    with nothing in the input revealing it: interpolating across the observed
    ordinal range places the highest lecture uploaded on the last day of term, so
    a student who uploads 11 of 20 lectures gets lecture 11 dated in May.

    So an ordinal produces a `candidate` on the plan and no write. Extraction
    is reliable and ordering is reliable; the date is not, and a
    confidently-weeks-wrong date rendered in a timeline is worse than an honest
    blank. The candidate is still computed, so the UI can offer it for one-click
    acceptance and so the work survives if the measurement later says it is fine.

    **What would reverse this:** a measured date accuracy for
    `inferred_filename`, on real course material whose real lecture dates are
    known. Not a larger filename corpus -- that measures extraction, which is
    already measured. See ROADMAP, Phase 3, slice 2.

    Nothing is cached. The interpolation depends on which ordinals happen to be
    present, so uploading one more lecture changes every suggestion in the
    course; a stored candidate would be stale on the next upload, and recomputing
    is correct by construction.

    Two passes, because they need different information. An explicit date is
    readable from one filename alone. An ordinal is not: `lecture-07.pdf` becomes
    a date only once the course's other filenames reveal that the numbering runs
    1..20, so every ordinal has to be collected before any of them resolves.

    Ordinals are grouped by kind and interpolated within their own group. A course
    holding twenty lectures and twelve recitations has two sequences that happen
    to share a term, and pooling them would put recitation 12 next to lecture 20
    at the end of the semester.

    **A manual date is never overwritten, even with `overwrite=True`.** That flag
    exists to re-run inference after fixing a course's term bounds; a person who
    typed a date in is the one source here that outranks this function, and
    replacing their answer with a guess is the single worst thing this code could
    do. Every other source is fair game -- re-inferring an `inferred_filename`
    date is just running a better version of the same guess.
    """
    course = await courses_repo.get(session, course_id, user_id)
    if course is None:
        raise NotFoundError(f"no course {course_id}")

    documents = await documents_repo.list_for_course(
        session, course_id=course_id, user_id=user_id
    )

    eligible: list[Document] = []
    plans: dict[uuid.UUID, DatePlan] = {}
    for document in documents:
        # `==`, not `is`. The column is `Mapped[str | None]` over `String(32)`,
        # so a loaded row carries a plain `str` and an identity check against the
        # StrEnum is silently always false -- which here would have meant
        # overwriting hand-set dates, the one thing this must never do.
        if document.occurred_at_source == OccurredAtSource.MANUAL:
            plans[document.id] = DatePlan(document, reason="dated by hand")
        elif document.occurred_at is not None and not overwrite:
            plans[document.id] = DatePlan(document, reason="already dated")
        else:
            eligible.append(document)

    # Pass 1: dates the filenames state outright.
    ordinals: dict[uuid.UUID, tuple[str, int]] = {}
    explicit: dict[uuid.UUID, date] = {}
    for document in eligible:
        name = _filename(document)
        if found := filename_dates.read_explicit_date(
            name, starts_on=course.starts_on, ends_on=course.ends_on
        ):
            explicit[document.id] = found
        elif ordinal := filename_dates.read_ordinal(name):
            ordinals[document.id] = ordinal

    # Pass 2: the observed range of each kind, which is what makes an ordinal
    # mean anything. A kind with one member has no range and stays undated --
    # see `interpolate`.
    seen: dict[str, list[int]] = {}
    for kind, number in ordinals.values():
        seen.setdefault(kind, []).append(number)

    for document in eligible:
        if found := explicit.get(document.id):
            plans[document.id] = DatePlan(
                document,
                occurred_on=found,
                source=OccurredAtSource.FILENAME_DATE,
                # Set even though this branch is a write, because a reader that
                # never runs the write needs a sentence for the empty cell. The
                # writer builds its own outcome and never reads this, so
                # `DatingOutcome`'s "reason exactly when undated" rule is intact.
                reason=f"the filename states {found}; nothing has stored it yet",
            )
            continue

        if ordinal := ordinals.get(document.id):
            kind, number = ordinal
            numbers = seen[kind]
            suggestion = filename_dates.interpolate(
                number,
                lowest=min(numbers),
                highest=max(numbers),
                starts_on=course.starts_on,
                ends_on=course.ends_on,
            )
            if suggestion is None:
                plans[document.id] = DatePlan(
                    document,
                    reason=(
                        f"only one {kind} in this course, so {kind} {number} "
                        f"has no range to sit in"
                    ),
                )
            else:
                # Computed, deliberately not written. See this function's
                # docstring: interpolated dates have no measured accuracy, so
                # they are offered rather than stored.
                plans[document.id] = DatePlan(
                    document,
                    candidates=(
                        DateCandidate(
                            source=OccurredAtSource.INFERRED_FILENAME,
                            occurred_on=suggestion,
                        ),
                    ),
                    reason=(
                        f"{kind} {number} of {min(numbers)}..{max(numbers)}; "
                        f"interpolated, so offered rather than stored"
                    ),
                )
            continue

        plans[document.id] = DatePlan(
            document, reason="no date or lecture number in the filename"
        )

    return CourseDatePlan(
        starts_on=course.starts_on,
        ends_on=course.ends_on,
        # Rebuilt in upload order rather than appended to, because the passes
        # above visit the documents in a different order than they arrived in.
        documents=tuple(plans[document.id] for document in documents),
    )


async def date_course_from_filenames(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    overwrite: bool = False,
) -> list[DatingOutcome]:
    """Date every document in a course from its filename. Reports what it could not.

    Plan, then write. Everything that decides *what* the date should be lives in
    `plan_dates_from_filenames`; this is only the part that commits it, which is
    what keeps the deciding half callable from a read request.

    Dates are written one at a time through `redate_document`, which commits per
    document. A run that dies halfway leaves the documents it reached correctly
    dated rather than rolling back work that was right; nothing here depends on
    the batch being atomic.
    """
    planned = await plan_dates_from_filenames(
        session, user_id=user_id, course_id=course_id, overwrite=overwrite
    )

    outcomes: list[DatingOutcome] = []
    for plan in planned.documents:
        document = plan.document
        if plan.occurred_on is None or plan.source is None:
            outcomes.append(
                DatingOutcome(
                    document_id=document.id,
                    filename=_filename(document),
                    candidates=plan.candidates,
                    reason=plan.reason,
                )
            )
            continue

        try:
            await redate_document(
                session,
                user_id=user_id,
                document_id=document.id,
                occurred_on=plan.occurred_on,
                source=plan.source,
            )
        except ServiceError as error:
            # Out-of-term, refused by the funnel. The filename stated a date in
            # the wrong term, which is a real thing that happens and must not
            # take the rest of the batch down with it. Caught here rather than in
            # the planner because it is the funnel's rule, and a planner that
            # anticipated it would be a second copy of a check that already
            # exists in the one place allowed to enforce it.
            outcomes.append(_undated(document, str(error)))
            continue

        outcomes.append(
            DatingOutcome(
                document_id=document.id,
                filename=_filename(document),
                occurred_on=plan.occurred_on,
                source=plan.source,
            )
        )

    return outcomes


async def parse_course_syllabus(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    data: bytes,
    name: str,
) -> ParsedSchedule:
    """Read a syllabus PDF into a schedule, against its course's term.

    Two lines of orchestration and nothing else -- the course supplies the term
    that fills in the years a schedule omits, and `parse_schedule` does the work.
    It is a service rather than four lines in the CLI because the parser is pure
    and the term is in the database, and that seam belongs on this side of the
    boundary rather than in every entrypoint that grows one.

    Writes nothing. A parsed schedule still has to go through
    `date_course_from_syllabus` to become dates.
    """
    course = await courses_repo.get(session, course_id, user_id)
    if course is None:
        raise NotFoundError(f"no course {course_id}")

    return parse_schedule(
        extract_pages(data, name=name),
        starts_on=course.starts_on,
        ends_on=course.ends_on,
    )


async def date_course_from_syllabus(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    schedule: Sequence[ScheduleEntry],
    overwrite: bool = False,
) -> list[DatingOutcome]:
    """Date a course's documents from its syllabus schedule.

    **The join is on the ordinal, not on topic text.** A syllabus gives an
    ordered, dated session list; a filename gives a session number. Matching
    `lecture 7` to `lecture 7` is exact, and slice 2 measured ordinal extraction
    on 70 real filenames at 58 correct / 12 undated / **0 wrong**. Matching a
    syllabus topic against a document's prose would be a similarity score --
    a new heuristic, with a new unmeasured error, replacing one that is already
    measured. There is no reason to take that trade.

    This is also what repairs slice 2's weak spot. Filename inference had to
    guess where lecture 7 fell by spreading ordinals across the term, which fails
    by weeks and is why those dates are offered rather than stored. A syllabus
    states the date outright, so the same ordinal that could only produce a
    suggestion now produces a fact -- `parsed_syllabus`, written.

    **Where the syllabus and the filename disagree, neither is stored.** A
    filename saying `2020-02-13` and a syllabus saying `2020-02-11` are two
    sources of testimony in genuine conflict: syllabi are published in advance
    and classes get moved, so the schedule is not automatically right, and a
    student's filename is not automatically right either. Both are returned as
    `candidates` for a person to settle in one click. Picking silently is exactly
    the failure this phase exists to prevent, and picking silently is *worse*
    here than having no date, because the conflict is evidence that one of the
    two sources is unreliable for this whole course.
    """
    course = await courses_repo.get(session, course_id, user_id)
    if course is None:
        raise NotFoundError(f"no course {course_id}")

    # What the schedule numbers, taken from the entries themselves rather than
    # passed in, so a caller cannot describe a schedule as something it is not.
    units = {entry.kind for entry in schedule}

    dates: dict[tuple[str, int], date] = {}
    for entry in schedule:
        key = (entry.kind, entry.ordinal)
        if dates.setdefault(key, entry.occurred_on) != entry.occurred_on:
            # A syllabus that dates lecture 7 twice, differently, cannot be
            # silently half-applied -- whichever row won would depend on order.
            raise ServiceError(
                f"the schedule gives {entry.kind} {entry.ordinal} two dates: "
                f"{dates[key]} and {entry.occurred_on}"
            )

    documents = await documents_repo.list_for_course(
        session, course_id=course_id, user_id=user_id
    )

    outcomes: list[DatingOutcome] = []
    for document in documents:
        if document.occurred_at_source == OccurredAtSource.MANUAL:
            outcomes.append(_undated(document, "dated by hand"))
            continue
        if document.occurred_at is not None and not overwrite:
            outcomes.append(_undated(document, "already dated"))
            continue

        name = _filename(document)
        ordinal = filename_dates.read_ordinal(name)
        scheduled = dates.get(ordinal) if ordinal else None
        stated = filename_dates.read_explicit_date(
            name, starts_on=course.starts_on, ends_on=course.ends_on
        )

        if scheduled and stated and scheduled != stated:
            outcomes.append(
                DatingOutcome(
                    document_id=document.id,
                    filename=name,
                    candidates=(
                        DateCandidate(
                            source=OccurredAtSource.PARSED_SYLLABUS,
                            occurred_on=scheduled,
                        ),
                        DateCandidate(
                            source=OccurredAtSource.FILENAME_DATE, occurred_on=stated
                        ),
                    ),
                    reason=(
                        f"the syllabus says {scheduled} but the filename says "
                        f"{stated}; not stored until someone decides"
                    ),
                )
            )
            continue

        if scheduled:
            occurred_on, source = scheduled, OccurredAtSource.PARSED_SYLLABUS
        elif stated:
            occurred_on, source = stated, OccurredAtSource.FILENAME_DATE
        elif ordinal:
            kind, number = ordinal
            outcomes.append(_undated(document, _no_such_session(kind, number, units)))
            continue
        else:
            outcomes.append(
                _undated(document, "no date or lecture number in the filename")
            )
            continue

        try:
            await redate_document(
                session,
                user_id=user_id,
                document_id=document.id,
                occurred_on=occurred_on,
                source=source,
            )
        except ServiceError as error:
            outcomes.append(_undated(document, str(error)))
            continue

        outcomes.append(
            DatingOutcome(
                document_id=document.id,
                filename=name,
                occurred_on=occurred_on,
                source=source,
            )
        )

    return outcomes


def _filename(document: Document) -> str:
    """The name the file was uploaded under.

    Read from `storage_key` rather than `title`, because `title` is settable on
    the upload form and defaults to the filename only when nobody overrode it.
    The key is the one place the original name survives intact.
    """
    return document.storage_key.rsplit("/", 1)[-1]


def _no_such_session(kind: str, number: int, units: set[str]) -> str:
    """Why an ordinal found nothing: a missing row, or a different unit entirely.

    **A schedule numbering weeks does not join to filenames numbering lectures,
    and is not converted so that it does.** Waterloo's schedule is headed `Week
    of`, so `(3) Sep 20` means the week beginning September 20th. A course with
    two lectures a week has lectures 5 and 6 inside week 3, and turning one into
    the other needs a lectures-per-week figure that appears nowhere -- not in the
    syllabus, not in the filenames, and derivable from the upload only by
    assuming the student uploaded every lecture, which is the assumption slice 2
    measured going wrong by weeks.

    The decisive objection is what it would do to `occurred_at_source`. A date
    reached that way would be stored as `parsed_syllabus`, meaning *the syllabus
    stated this date*, when the syllabus stated no such thing. That is a false
    claim in the one column this phase exists to keep honest, and it is worse
    than the interpolation slice 2 declined to store, because it would carry the
    strongest provenance value the enum has.

    A week does bound a lecture to seven days, which is tighter than
    interpolation manages, so an offered candidate looks tempting. It needs the
    same missing figure to know *which* week, so the bound is only ever as good
    as the guess that picks it and nothing is gained.

    **What would reverse this:** a lectures-per-week on `courses` that a person
    enters, making week-to-lecture arithmetic over stated facts, or filenames
    carrying weekdays. Not an inference of that figure from the files present.

    So the join misses, and this says why it missed. Reporting a weekly schedule
    as `the syllabus has no lecture 7` would send someone looking for a row that
    was never supposed to be there.
    """
    if len(units) == 1 and (unit := next(iter(units))) != kind:
        return (
            f"the syllabus numbers {unit}s and this filename numbers {kind}s; "
            f"one {unit} can hold several {kind}s and nothing states how many"
        )
    return f"the syllabus has no {kind} {number}"


def _undated(document: Document, reason: str) -> DatingOutcome:
    return DatingOutcome(
        document_id=document.id,
        filename=_filename(document),
        occurred_on=None,
        source=None,
        reason=reason,
    )


async def redate_document(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    occurred_on: date,
    source: OccurredAtSource,
) -> RedateResult:
    """Set when this document happened, and where that came from.

    **This is the only function that writes `documents.occurred_at`.** From Phase
    5 on it must also update `concept_mentions.occurred_at` for this document,
    in this same transaction, because that column is a denormalised copy of this
    one and nothing in the database can catch the two disagreeing. When that code
    is added it goes *here*, between the write below and the commit.

    A `date` in, a timestamp out. All four sources are day-granular -- a syllabus
    names a day, a filename names a day, and a person picking a date on a form
    picks a day -- so accepting a datetime would invite callers to invent a time
    of day that no source knows. It is stored at midnight UTC.

    Out-of-term dates are refused, with one exception. An interpolated or parsed
    date landing outside the course's own bounds is the heuristic being wrong, and
    the phase's rule is that a wrong date is worse than no date. A *manual* date
    is different: the user is the authority on when their lecture happened, and if
    they insist on a date outside the term then the term is what is wrong.
    Refusing it would leave a document nobody can fix. It is stored, flagged on
    the way out, and logged -- so a course with bad bounds shows up as a pattern
    rather than as one odd document.

    This function owns its transaction. The commit is here rather than in callers
    because Phase 5's cascade has to be atomic with the write above it, and a
    boundary that every caller has to remember is a boundary that one of them will
    not.
    """
    document = await documents_repo.get(session, document_id, user_id)
    if document is None:
        raise NotFoundError(f"no document {document_id}")

    course = await courses_repo.get(session, document.course_id, user_id)
    if course is None:
        # Unreachable through the composite foreign key, which pins a document to
        # a course owned by the same user. Checked anyway because the alternative
        # to a clear error here is an AttributeError on `None` two lines down.
        raise NotFoundError(f"no course {document.course_id}")

    outside_term = not (course.starts_on <= occurred_on <= course.ends_on)
    if outside_term and source is not OccurredAtSource.MANUAL:
        raise ServiceError(
            f"{source} date {occurred_on} falls outside {course.name}'s term "
            f"({course.starts_on}..{course.ends_on}); refusing to store it"
        )

    if outside_term:
        logger.warning(
            "manual date %s for document %s is outside %s's term (%s..%s) -- "
            "stored anyway; check the course's bounds",
            occurred_on,
            document_id,
            course.name,
            course.starts_on,
            course.ends_on,
        )

    await documents_repo.set_occurred_at(
        session,
        document_id=document_id,
        user_id=user_id,
        occurred_at=datetime.combine(occurred_on, time.min, tzinfo=UTC),
        source=source,
    )

    # Phase 5: the cascade to `concept_mentions` goes here, before the commit.
    await session.commit()

    await session.refresh(document)
    return RedateResult(
        document=document,
        starts_on=course.starts_on,
        ends_on=course.ends_on,
        outside_term=outside_term,
    )
