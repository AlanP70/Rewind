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
