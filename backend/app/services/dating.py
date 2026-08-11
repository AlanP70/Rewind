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
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, OccurredAtSource
from app.repositories import courses as courses_repo
from app.repositories import documents as documents_repo
from app.services import filename_dates
from app.services.errors import NotFoundError, ServiceError

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
class DatingOutcome:
    """What the filename dater decided about one document, and why.

    Three shapes, and the middle one is the point:

      `occurred_on` set   -- a date was written, and `source` says how.
      `suggestion` set    -- a date was computed and deliberately *not* written.
                             The document is still undated. `reason` says why it
                             was only offered.
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
    suggestion: date | None = None
    reason: str = ""


async def date_course_from_filenames(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    overwrite: bool = False,
) -> list[DatingOutcome]:
    """Date every document in a course from its filename. Reports what it could not.

    **Only a date the filename states is written. An interpolated one is offered.**
    A `filename_date` is testimony -- someone wrote `2020-02-11` in the name, and
    it is wrong only if they were wrong. An `inferred_filename` date is arithmetic
    on top of testimony about something else, its accuracy has never been
    measured against real lecture dates, and it fails by *weeks* rather than days
    with nothing in the input revealing it: interpolating across the observed
    ordinal range places the highest lecture uploaded on the last day of term, so
    a student who uploads 11 of 20 lectures gets lecture 11 dated in May.

    So an ordinal produces a `suggestion` on the outcome and no write. Extraction
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

    Dates are written one at a time through `redate_document`, which commits per
    document. A run that dies halfway leaves the documents it reached correctly
    dated rather than rolling back work that was right; nothing here depends on
    the batch being atomic.
    """
    course = await courses_repo.get(session, course_id, user_id)
    if course is None:
        raise NotFoundError(f"no course {course_id}")

    documents = await documents_repo.list_for_course(
        session, course_id=course_id, user_id=user_id
    )

    candidates: list[Document] = []
    outcomes: list[DatingOutcome] = []
    for document in documents:
        # `==`, not `is`. The column is `Mapped[str | None]` over `String(32)`,
        # so a loaded row carries a plain `str` and an identity check against the
        # StrEnum is silently always false -- which here would have meant
        # overwriting hand-set dates, the one thing this must never do.
        if document.occurred_at_source == OccurredAtSource.MANUAL:
            outcomes.append(_undated(document, "dated by hand"))
        elif document.occurred_at is not None and not overwrite:
            outcomes.append(_undated(document, "already dated"))
        else:
            candidates.append(document)

    # Pass 1: dates the filenames state outright.
    ordinals: dict[uuid.UUID, tuple[str, int]] = {}
    explicit: dict[uuid.UUID, date] = {}
    for document in candidates:
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

    for document in candidates:
        if found := explicit.get(document.id):
            try:
                await redate_document(
                    session,
                    user_id=user_id,
                    document_id=document.id,
                    occurred_on=found,
                    source=OccurredAtSource.FILENAME_DATE,
                )
            except ServiceError as error:
                # Out-of-term, refused by the funnel. The filename stated a date
                # in the wrong term, which is a real thing that happens and must
                # not take the rest of the batch down with it.
                outcomes.append(_undated(document, str(error)))
                continue

            outcomes.append(
                DatingOutcome(
                    document_id=document.id,
                    filename=_filename(document),
                    occurred_on=found,
                    source=OccurredAtSource.FILENAME_DATE,
                )
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
                outcomes.append(
                    _undated(
                        document,
                        f"only one {kind} in this course, so {kind} {number} "
                        f"has no range to sit in",
                    )
                )
            else:
                # Computed, deliberately not written. See this function's
                # docstring: interpolated dates have no measured accuracy, so
                # they are offered rather than stored.
                outcomes.append(
                    DatingOutcome(
                        document_id=document.id,
                        filename=_filename(document),
                        suggestion=suggestion,
                        reason=(
                            f"{kind} {number} of {min(numbers)}..{max(numbers)}; "
                            f"interpolated, so offered rather than stored"
                        ),
                    )
                )
            continue

        outcomes.append(_undated(document, "no date or lecture number in the filename"))

    return outcomes


def _filename(document: Document) -> str:
    """The name the file was uploaded under.

    Read from `storage_key` rather than `title`, because `title` is settable on
    the upload form and defaults to the filename only when nobody overrode it.
    The key is the one place the original name survives intact.
    """
    return document.storage_key.rsplit("/", 1)[-1]


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
