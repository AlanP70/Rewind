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

1. `resolve_document` -- preconditions. Unknown course, path outside the repo,
   existing chunks without `force`. These are complaints about the request, not
   processing failures, so they raise before any run exists.
2. Open the run at `running`, commit. From here on, every exit updates it.
3. Chunk, then embed.
4. Success: run `succeeded`. Failure: the document goes to `failed` and the run
   records why, both committed, and the error is re-raised.

A document is therefore never left at `processing` with no live run -- the same
rule Phase 1 applied to a half-filled embedding column, for the same reason.
"""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentStatus, RunStatus
from app.repositories import documents as documents_repo
from app.repositories import processing_runs as runs_repo
from app.services.embedding import EmbeddingResult, embed_document, estimate, pending_chunks
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


async def process_document(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    path: Path,
    kind: str,
    title: str,
    force: bool = False,
    embed: bool = True,
) -> ProcessResult:
    """Process one document, recording the attempt. See the module docstring."""
    resolved = await resolve_document(
        session,
        user_id=user_id,
        course_id=course_id,
        path=path,
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
        # `resolved` survives the commits above -- the session is configured
        # `expire_on_commit=False`.
        ingested = await ingest_document(session, resolved=resolved, path=path)
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
