"""Response bodies for the document routes.

Pydantic models rather than the services' dataclasses, because these are a
published contract: the frontend reads them, and a field renamed in a service
should not silently rename itself in JSON.
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.models import DocumentStatus, OccurredAtSource, RunStatus


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

    # From the most recent run. An upload opens its run at `queued` before it
    # answers, so these are populated from the moment `POST /documents` returns --
    # `run_status` is `queued` until a worker claims it, not null. Null is now
    # only reachable for a document whose row was created without ever being
    # submitted for processing.
    attempts: int | None
    run_status: RunStatus | None
    error: str | None

    # True when the latest run has gone quiet: `running` with no worker reporting,
    # or still `queued` long after it was enqueued, which is what a dropped job
    # looks like from here.
    stale: bool


class DocumentDateUpdate(BaseModel):
    """A user correcting a document's date by hand.

    A `date`, not a datetime. Every source of a date in this system is
    day-granular, and a form that asks for a time would be asking the user to
    invent one. See `services.dating.redate_document`.

    No `source` field, deliberately. This endpoint *is* the manual path, so the
    provenance is a property of how the date arrived rather than something the
    caller declares -- a client that could send `parsed_syllabus` could launder a
    guess into a fact, which is the one thing `occurred_at_source` exists to stop.
    """

    occurred_on: date


class DocumentDate(BaseModel):
    """A document's date and the honest account of where it came from."""

    document_id: uuid.UUID

    # Both null together or neither, enforced by
    # `ck_documents_occurred_at_has_source`. An undated document is a real state
    # the UI must render -- it is what "surfaced, never silently defaulted" means.
    occurred_at: datetime | None
    occurred_at_source: OccurredAtSource | None

    # The course's term, echoed back so the UI can show the range a date is being
    # judged against without a second request.
    starts_on: date
    ends_on: date

    # The date was accepted and sits outside that term. Only reachable manually:
    # every other source is refused when it lands outside. A UI that ignores this
    # turns a user's correction into a silently odd row.
    outside_term: bool
