"""Upload a document and ask how it is getting on.

Both routes translate and nothing else: parse the request, call one service,
turn a `ServiceError` into the right status code. No queries and no decisions --
see `CLAUDE.md`'s layering rule.
"""

import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue, get_session
from app.models import OccurredAtSource
from app.models.user import SEED_USER_ID
from app.schemas.documents import (
    CourseDating,
    DateCandidate,
    DocumentAccepted,
    DocumentDate,
    DocumentDateUpdate,
    DocumentDating,
    DocumentProgress,
)
from app.services.dating import DatePlan, plan_dates_from_filenames, redate_document
from app.services.errors import ConflictError, NotFoundError, ServiceError
from app.services.processing import document_progress, submit_document

router = APIRouter(prefix="/documents", tags=["documents"])

# 4xx only. A `ServiceError` is by definition something the caller can fix, so
# there is no 5xx entry -- anything else keeps its traceback and becomes a 500.
_STATUS_FOR = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
}


def _as_http(error: ServiceError) -> HTTPException:
    """The message is passed through as `detail` verbatim.

    Service messages are already written to be read by a person -- that is the
    phase's "readable error" bar -- so rewording them here would produce two
    descriptions of one failure that drift apart.
    """
    return HTTPException(
        status_code=_STATUS_FOR.get(type(error), status.HTTP_400_BAD_REQUEST),
        detail=str(error),
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    session: Annotated[AsyncSession, Depends(get_session)],
    queue: Annotated[ArqRedis, Depends(get_queue)],
    course_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "lecture",
    title: Annotated[str | None, Form()] = None,
    force: Annotated[bool, Form()] = False,
    embed: Annotated[bool, Form()] = True,
) -> DocumentAccepted:
    """Accept a PDF and queue it. **202, not 201** -- the document exists but is
    not yet what the client asked for, and will not be for a while.

    `embed` defaults on, because a document without vectors cannot be searched and
    so never reaches `ready`. It is exposed so repeated testing against the same
    PDF does not have to be paid for each time; it is not a normal thing to send.

    `user_id` is the hardcoded seed user until Phase 7 brings auth. It is not a
    field on this form and must never become one -- a client-supplied owner is a
    client-supplied authorisation.
    """
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="the uploaded file is empty"
        )

    try:
        result = await submit_document(
            session,
            queue,
            user_id=SEED_USER_ID,
            course_id=course_id,
            # `storage_key` strips any directory component, so a filename is never
            # trusted as a path. See `core/storage.py`.
            filename=file.filename or "upload.pdf",
            data=data,
            kind=kind,
            title=title or _default_title(file.filename),
            force=force,
            embed=embed,
        )
    except ServiceError as error:
        raise _as_http(error) from error

    return DocumentAccepted(
        document_id=result.document_id,
        job_id=result.job_id,
        reused_document=result.reused_document,
    )


@router.get("")
async def list_course_documents(
    session: Annotated[AsyncSession, Depends(get_session)],
    course_id: uuid.UUID,
) -> CourseDating:
    """Every document in a course, with its date or the reason it has none.

    Calls the **planner**, which writes nothing, rather than the dater. A GET that
    reached `redate_document` would date a whole course as a side effect of
    someone opening a page, and the protection is that
    `plan_dates_from_filenames` contains no write at all -- not a flag on the
    function that does.

    Candidates are therefore recomputed on every request. That is deliberate:
    interpolating an ordinal depends on which files have been uploaded, so a
    cached suggestion is wrong as soon as one more lecture arrives.
    """
    try:
        planned = await plan_dates_from_filenames(
            session, user_id=SEED_USER_ID, course_id=course_id
        )
    except ServiceError as error:
        raise _as_http(error) from error

    return CourseDating(
        starts_on=planned.starts_on,
        ends_on=planned.ends_on,
        undated=sum(
            1 for plan in planned.documents if plan.document.occurred_at is None
        ),
        documents=[_dating(plan) for plan in planned.documents],
    )


@router.get("/{document_id}/status")
async def document_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    document_id: uuid.UUID,
) -> DocumentProgress:
    """What is happening to this document. Safe to poll.

    A document that failed is still 200 with `status: failed` and the error in the
    body. The request to *know* succeeded, which is what the status code is about;
    a 4xx here would make a client's error handling fire on a perfectly good
    answer. Only an unknown document is a 404.
    """
    try:
        return await document_progress(
            session, user_id=SEED_USER_ID, document_id=document_id
        )
    except ServiceError as error:
        raise _as_http(error) from error


@router.patch("/{document_id}/date")
async def correct_document_date(
    session: Annotated[AsyncSession, Depends(get_session)],
    document_id: uuid.UUID,
    body: DocumentDateUpdate,
) -> DocumentDate:
    """Set a document's date by hand. The `manual` path of invariant 4.

    PATCH rather than PUT: this replaces one field of a document, not the
    document. A date outside the course's term is accepted here and nowhere else
    -- see `redate_document` -- so a 200 with `outside_term: true` is a real
    answer this route can give, not an error the client should retry.
    """
    try:
        result = await redate_document(
            session,
            user_id=SEED_USER_ID,
            document_id=document_id,
            occurred_on=body.occurred_on,
            source=OccurredAtSource.MANUAL,
        )
    except ServiceError as error:
        raise _as_http(error) from error

    return DocumentDate(
        document_id=result.document.id,
        occurred_at=result.document.occurred_at,
        occurred_at_source=result.document.occurred_at_source,
        starts_on=result.starts_on,
        ends_on=result.ends_on,
        outside_term=result.outside_term,
    )


def _dating(plan: DatePlan) -> DocumentDating:
    """One plan as one row. A mapping with no decisions -- see `DatePlan.offers`."""
    return DocumentDating(
        document_id=plan.document.id,
        filename=plan.filename,
        title=plan.document.title,
        status=plan.document.status,
        occurred_at=plan.document.occurred_at,
        occurred_at_source=plan.document.occurred_at_source,
        candidates=[
            DateCandidate(source=offer.source, occurred_on=offer.occurred_on)
            for offer in plan.offers
        ],
        reason=plan.reason,
    )


def _default_title(filename: str | None) -> str:
    """The filename without its extension, matching the CLI's default."""
    return (filename or "upload.pdf").rsplit(".", 1)[0]
