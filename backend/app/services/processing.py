"""One document, end to end, with a `processing_runs` row recording the attempt.

This is the function both entrypoints call: the CLI runs it inline, and from
slice 3 the arq worker runs it in a background process. Anything that only one of
them does belongs in that caller, not here.

**This service commits**, like `embedding` and unlike the rest of them. It has to:
the whole point of the run row is that it is durable *while* the work is still
happening, so something looking at the database mid-job can see an attempt in
flight. A run row that only appears once the work is over records history nobody
can act on.

The shape of an attempt:

1. `resolve_document` -- preconditions. Unknown course, existing chunks without
   `force`. These are complaints about the request, not processing failures, so
   they raise before any run exists.
2. Open the run at `running`, commit. From here on, every exit updates it.
3. Download the bytes, chunk, then embed. The download is inside the run because
   it is failure-prone in exactly the way the run row exists to record -- a key
   that is not in the bucket, or a bucket that cannot be reached.
4. Success: run `succeeded`. Failure: the document goes to `failed` and the run
   records why, both committed, and the error is re-raised.

A document is therefore never left at `processing` with no live run -- the same
rule Phase 1 applied to a half-filled embedding column, for the same reason.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.storage import get_storage, storage_key as build_storage_key
from app.models import DocumentStatus, RunStatus
from app.repositories import chunks as chunks_repo
from app.repositories import documents as documents_repo
from app.repositories import processing_runs as runs_repo
from app.schemas.documents import DocumentProgress
from app.services.embedding import EmbeddingResult, embed_document, estimate, pending_chunks
from app.services.errors import NotFoundError
from app.services.ingestion import IngestResult, ingest_document, resolve_document

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessResult:
    document_id: uuid.UUID
    run_id: uuid.UUID
    attempts: int
    ingest: IngestResult
    # None when embedding was skipped. The document then stays at `processing`,
    # which is honest: it has chunks but cannot be searched.
    embedding: EmbeddingResult | None


@dataclass(frozen=True)
class SubmitResult:
    document_id: uuid.UUID
    job_id: str
    reused_document: bool


async def submit_document(
    session: AsyncSession,
    queue: ArqRedis,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    filename: str,
    data: bytes,
    kind: str,
    title: str,
    force: bool = False,
    embed: bool = True,
) -> SubmitResult:
    """Accept an uploaded PDF and hand it to the worker. Returns immediately.

    The counterpart to `process_document`, not a variant of it: this one decides
    whether the work should happen and records the intent to do it;
    `process_document` does the work. They are separate because the caller here
    is an HTTP request that must answer in milliseconds.

    The order of the four steps is the whole design, and each boundary is chosen:

    1. **Resolve first, before uploading a single byte.** Preconditions -- unknown
       course, chunks present without `force` -- are complaints about the request,
       so they raise here and become a 4xx with nothing enqueued and no run row.
       This is the same rule Phase 2 already applied to `processing_runs`, and it
       lands on HTTP exactly: a request that was never valid leaves no history of
       an attempt, because there was no attempt.
    2. **Upload.** Only now, once the request is known to be worth honouring, so a
       rejected upload cannot strand an object nobody will ever ask for. The CLI
       uploads before it resolves and can leave one behind; it has no separate
       resolve step to put first, and the API does.
    3. **Commit.** The row and the object become visible together.
    4. **Enqueue**, last, because a job whose document is not yet committed can be
       picked up by a worker that cannot see it.

    If the enqueue itself fails, the caller gets a 5xx and the document sits at
    `pending` with its bytes already stored -- so re-uploading resolves to the
    same row, finds no chunks, and enqueues again. That is the intended repair,
    and it works because Postgres holds the intent and Redis only carries the
    request.
    """
    storage_key = build_storage_key(user_id, filename)

    resolved = await resolve_document(
        session,
        user_id=user_id,
        course_id=course_id,
        storage_key=storage_key,
        kind=kind,
        title=title,
        force=force,
    )

    await get_storage().upload(storage_key, data)
    await session.commit()

    job = await queue.enqueue_job(
        "process_document_task",
        user_id=str(user_id),
        course_id=str(course_id),
        storage_key=storage_key,
        kind=kind,
        title=title,
        embed=embed,
    )
    # arq returns None when a job with the same id already exists. Nothing here
    # sets a job id, so ids are generated and this cannot happen -- but the type
    # is optional, and asserting it would crash the request rather than explain.
    #
    # Not a `ServiceError`: those are things the caller can fix, which is what
    # lets the route map every one of them to a 4xx. This is the server failing,
    # so it must become a 500 -- and the repair is re-uploading, which resolves to
    # the same row and enqueues again.
    if job is None:
        raise RuntimeError(f"could not enqueue {storage_key}; it may already be queued")

    logger.info(
        "queued %s as document %s (job %s)", storage_key, resolved.document.id, job.job_id
    )
    return SubmitResult(
        document_id=resolved.document.id,
        job_id=job.job_id,
        reused_document=resolved.reused,
    )


async def process_document(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    storage_key: str,
    kind: str,
    title: str,
    force: bool = False,
    embed: bool = True,
) -> ProcessResult:
    """Process one document, recording the attempt. See the module docstring.

    Takes a storage key, never a path or bytes. The worker in slice 3 receives
    nothing else -- it is a separate process that never saw the upload -- and the
    CLI hands over the same thing so both entrypoints exercise one code path.
    """
    resolved = await resolve_document(
        session,
        user_id=user_id,
        course_id=course_id,
        storage_key=storage_key,
        kind=kind,
        title=title,
        force=force,
    )
    document_id = resolved.document.id
    # Committed before the run opens, so the document a failed run points at is
    # guaranteed to exist even if the attempt dies immediately.
    await session.commit()

    attempts = await runs_repo.next_attempt_number(session, document_id)
    run = await runs_repo.create(
        session,
        user_id=user_id,
        document_id=document_id,
        attempts=attempts,
        status=RunStatus.RUNNING,
    )
    run_id = run.id
    await session.commit()

    try:
        data = await get_storage().download(storage_key)
        # `resolved` survives the commits above -- the session is configured
        # `expire_on_commit=False`.
        ingested = await ingest_document(session, resolved=resolved, data=data)
        await session.commit()
        logger.info(
            "%s document %s (attempt %d): %d pages, %d chunks%s",
            "re-ingested" if ingested.reused_document else "ingested",
            document_id,
            attempts,
            ingested.page_count,
            ingested.chunk_count,
            f", replaced {ingested.replaced_chunks}" if ingested.replaced_chunks else "",
        )

        if embed:
            embedded = await embed_with_estimate(
                session, user_id=user_id, document_id=document_id
            )
        else:
            embedded = None
            logger.info("embeddings skipped; document stays at %s", DocumentStatus.PROCESSING)
    except Exception as error:
        await session.rollback()
        await documents_repo.set_status(session, document_id, DocumentStatus.FAILED)
        # Some exceptions stringify to nothing, which would store a blank error
        # and satisfy the NOT NULL check while telling a reader nothing.
        await runs_repo.mark_failed(session, run_id, str(error) or type(error).__name__)
        await session.commit()
        raise

    await runs_repo.mark_succeeded(session, run_id)
    await session.commit()

    return ProcessResult(
        document_id=document_id,
        run_id=run_id,
        attempts=attempts,
        ingest=ingested,
        embedding=embedded,
    )


async def document_progress(
    session: AsyncSession, *, user_id: uuid.UUID, document_id: uuid.UUID
) -> DocumentProgress:
    """Everything the polling client needs, in one read.

    Reads Postgres and never Redis, which is the point rather than a convenience.
    Render's free Key Value plan has no persistence, so the queue can lose every
    job it is holding; the `documents` and `processing_runs` rows cannot. A status
    endpoint answering from job state would report "no such job" for work that is
    still owed.

    `stale` is derived here rather than stored. A stored flag needs something to
    write it -- a sweeper, on a timer, whose failure mode is silence -- whereas a
    comparison against `started_at` is correct the instant it is read and costs
    nothing until someone asks. Nothing acts on it: it is a hint that a run which
    claims to be `running` has probably lost its worker, and slice 4's UI is what
    surfaces that.
    """
    document = await documents_repo.get(session, document_id, user_id)
    if document is None:
        raise NotFoundError(f"no document {document_id} for this user")

    total = await chunks_repo.count_for_document(session, document_id)
    unembedded = await chunks_repo.count_unembedded(session, document_id)
    run = await runs_repo.latest_for_document(session, document_id)

    stale = False
    if run is not None and run.status == RunStatus.RUNNING and run.started_at is not None:
        age = datetime.now(UTC) - run.started_at
        stale = age.total_seconds() > settings.stale_run_after_seconds

    return DocumentProgress(
        document_id=document_id,
        status=document.status,
        chunks_total=total,
        # Derived rather than counted separately: one fewer query, and the two
        # numbers cannot disagree the way two independent counts could if a batch
        # commits between them.
        chunks_embedded=total - unembedded,
        attempts=run.attempts if run else None,
        run_status=run.status if run else None,
        error=run.error if run else None,
        stale=stale,
    )


async def embed_with_estimate(
    session: AsyncSession, *, user_id: uuid.UUID, document_id: uuid.UUID
) -> EmbeddingResult:
    """Embed, logging what the run is about to cost before it is spent.

    Public because the CLI's standalone `embed` command wants the same estimate
    line, and duplicating the formatting is how the two drift apart.
    """
    pending = await pending_chunks(session, document_id)
    if pending:
        cost = estimate(pending)
        # Two decimal places would render a real cost of $0.000033 as $0.00, which
        # is the number this line exists to show.
        dollars = (
            f"${cost.estimated_usd:.2f}"
            if cost.estimated_usd >= 0.01
            else f"${cost.estimated_usd:.6f}"
        )
        logger.info(
            "embedding %d chunk(s), ~%d tokens, ~%s (estimate)",
            cost.chunk_count,
            cost.estimated_tokens,
            dollars,
        )
    else:
        logger.info("embeddings already complete, nothing to spend")

    result = await embed_document(session, user_id=user_id, document_id=document_id)
    logger.info(
        "embedded %d, remaining %d, status %s",
        result.embedded,
        result.remaining,
        result.status,
    )
    return result
