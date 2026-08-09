"""Response bodies for the document routes.

Pydantic models rather than the services' dataclasses, because these are a
published contract: the frontend reads them, and a field renamed in a service
should not silently rename itself in JSON.
"""

import uuid

from pydantic import BaseModel

from app.models import DocumentStatus, RunStatus


class DocumentAccepted(BaseModel):
    """The 202 body. Deliberately small.

    `document_id` is the only thing the client needs -- it is what
    `GET /documents/{id}/status` takes, and polling that is how anything else
    about the document is learned. `job_id` is included for debugging only:
    Postgres is the record of what is happening, and a client that polls Redis
    for job state would be reading the one store that is allowed to lose it.
    """

    document_id: uuid.UUID
    job_id: str
    reused_document: bool


class DocumentProgress(BaseModel):
    """What the polling UI reads.

    Counts, not a percentage. Until chunking finishes there are no chunks, so any
    single number would have to invent progress for the extraction phase -- and an
    invented number gets rendered confidently and debugged later as if it meant
    something. Two honest counts let slice 4's UI decide what to show.
    """

    document_id: uuid.UUID
    status: DocumentStatus
    chunks_total: int
    chunks_embedded: int

    # From the most recent run. Null before any run has opened -- a document that
    # is queued but not yet picked up.
    attempts: int | None
    run_status: RunStatus | None
    error: str | None

    stale: bool
