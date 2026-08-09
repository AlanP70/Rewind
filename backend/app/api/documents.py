"""Upload a document and ask how it is getting on.

Both routes translate and nothing else: parse the request, call one service,
turn a `ServiceError` into the right status code. No queries and no decisions --
see `CLAUDE.md`'s layering rule.
"""

import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session
from app.models.user import SEED_USER_ID
from app.schemas.documents import DocumentAccepted, DocumentProgress
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


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


def get_queue(request: Request) -> ArqRedis:
    """The pool opened in the app's lifespan. See `core/queue.py`."""
    return request.app.state.queue


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


def _default_title(filename: str | None) -> str:
    """The filename without its extension, matching the CLI's default."""
    return (filename or "upload.pdf").rsplit(".", 1)[0]
