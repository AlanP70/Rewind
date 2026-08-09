"""The arq task. One function, and it delegates almost immediately.

The body is thin on purpose. It exists to translate between arq's vocabulary --
a job, a retry count, an exception that means "run me again" -- and the service
layer's, which knows nothing about queues. Every decision about what processing a
document *means* lives in `process_document`, which the CLI calls with the same
arguments. A worker that reimplemented any of it would be a second code path that
only fails in production.
"""

import logging
import uuid

from arq.worker import Retry

from app.core.db import async_session
from app.services.errors import ServiceError
from app.services.processing import process_document

logger = logging.getLogger(__name__)

# Three attempts at a transient failure. Lives here rather than on
# `WorkerSettings` because the task itself has to know the ceiling -- see the
# retry comment below -- and `settings.py` imports this module, so putting it the
# other way round would be a cycle.
MAX_TRIES = 3


async def process_document_task(
    ctx: dict,
    *,
    user_id: str,
    course_id: str,
    storage_key: str,
    kind: str,
    title: str,
    embed: bool = True,
) -> str | None:
    """Process one uploaded document. Returns the document id, or None if it
    failed permanently.

    Ids arrive as strings because they have been through Redis. arq pickles job
    arguments, so `uuid.UUID` would survive the round trip -- but the job payload
    is then only readable by Python holding the same classes, and the one time
    anyone reads a queued payload by hand is when something has gone wrong.
    """
    async with async_session() as session:
        try:
            result = await process_document(
                session,
                user_id=uuid.UUID(user_id),
                course_id=uuid.UUID(course_id),
                storage_key=storage_key,
                kind=kind,
                title=title,
                # Always `True` here, and this is not the flag the uploader set.
                #
                # `force` answers two different questions in the two callers. To
                # the uploader it means "yes, replace the chunks of a document I
                # already ingested" -- a decision about intent, which the route
                # settles before enqueuing anything, with a 409 if the answer is
                # no. Inside the worker there is no intent left to check: the job
                # exists, so the decision was made. What is left is delivery.
                #
                # arq is at-least-once, so this body will sometimes run twice on
                # one document -- a retry after a transient failure, or a job
                # re-run after a hard kill. Attempt 1 can have written chunks and
                # then died embedding them. With `force=False`, attempt 2 would
                # hit "chunks exist, set force" and fail *permanently* on a
                # problem that was transient, which is the exact inversion the
                # retry rules exist to prevent. `ingest_document` deletes and
                # rebuilds in one transaction, so replacing is always safe.
                force=True,
                embed=embed,
            )
        except ServiceError as error:
            # Permanent: no text layer, no such course, a key with no object.
            # Returning rather than raising is what makes it permanent, given how
            # arq actually behaves (see below). It would fail identically on
            # attempts 2 and 3 while making a deterministic problem look like
            # flakiness.
            #
            # Swallowing it loses nothing. `process_document` has already written
            # the failure to `processing_runs` and moved the document to `failed`,
            # in its own committed transaction -- Postgres is the record of what
            # happened, and Redis only ever carried the request to do it. The
            # status endpoint reads the former.
            logger.error("document %s failed permanently: %s", storage_key, error)
            return None
        except Exception:
            # Transient: a dropped connection, an OpenAI 5xx or 429, storage
            # unreachable. Retried -- but the retry has to be *asked for*.
            #
            # arq does not retry on an arbitrary exception. It retries on `Retry`,
            # `RetryJob` and `CancelledError`; anything else marks the job
            # finished and failed (`worker.py`, `run_job`). `max_tries` bounds
            # retries that were requested, it does not create them. Simply
            # letting the exception propagate -- which reads like the obvious way
            # to signal "this failed, try again" -- gives one attempt and no
            # retries at all, silently turning every transient failure into a
            # permanent one. That is the inversion of the whole classification.
            #
            # The ceiling is checked here rather than left to arq so the last
            # attempt fails as itself: re-raising means Postgres records a failed
            # run *and* the worker log carries the traceback. Deferring past the
            # ceiling instead would have arq drop a phantom attempt with a
            # "max retries exceeded" line and no exception to read.
            if ctx["job_try"] >= MAX_TRIES:
                logger.error(
                    "document %s failed on attempt %d of %d; giving up",
                    storage_key,
                    ctx["job_try"],
                    MAX_TRIES,
                )
                raise
            # Seconds, doubling: 2s then 4s. Long enough for a redeploy or a rate
            # limit to clear, short enough that the polling UI in slice 4 does not
            # look hung.
            raise Retry(defer=2 ** ctx["job_try"])

    return str(result.document_id)
