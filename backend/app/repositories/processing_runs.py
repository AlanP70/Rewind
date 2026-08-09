"""Queries against `processing_runs`. The only place run SQL is written."""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProcessingRun, RunStatus


async def next_attempt_number(session: AsyncSession, document_id: uuid.UUID) -> int:
    """What number the next attempt at this document gets. 1-based.

    Monotonic per document rather than per job: a re-upload months later
    continues the numbering. `UNIQUE (document_id, attempts)` is what turns a
    racing double-write into an error instead of two rows both calling
    themselves attempt 2.
    """
    result = await session.execute(
        select(func.coalesce(func.max(ProcessingRun.attempts), 0)).where(
            ProcessingRun.document_id == document_id
        )
    )
    return result.scalar_one() + 1


async def list_for_document(
    session: AsyncSession, document_id: uuid.UUID
) -> list[ProcessingRun]:
    """Every attempt at this document, oldest first."""
    result = await session.execute(
        select(ProcessingRun)
        .where(ProcessingRun.document_id == document_id)
        .order_by(ProcessingRun.attempts)
    )
    return list(result.scalars().all())


async def latest_for_document(
    session: AsyncSession, document_id: uuid.UUID
) -> ProcessingRun | None:
    """The most recent attempt, or None if none has been opened yet.

    Ordered by `attempts` rather than `started_at`: the attempt number is what
    `UNIQUE (document_id, attempts)` makes strictly increasing, whereas two runs
    opened in the same millisecond could tie on a timestamp. A separate query
    rather than the last element of `list_for_document`, which would load every
    attempt to read one -- and the status endpoint is the thing being polled.
    """
    result = await session.execute(
        select(ProcessingRun)
        .where(ProcessingRun.document_id == document_id)
        .order_by(ProcessingRun.attempts.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    attempts: int,
    status: str,
) -> ProcessingRun:
    """Open a run.

    `status` is a parameter because the two callers start in different places:
    the CLI runs the work itself and opens at `running`, while an enqueued job
    opens at `queued` and is picked up later. `started_at` follows from the
    status -- the CHECK constraint will not accept them disagreeing.
    """
    run = ProcessingRun(
        user_id=user_id,
        document_id=document_id,
        attempts=attempts,
        status=status,
        started_at=None if status == RunStatus.QUEUED else func.now(),
    )
    session.add(run)
    await session.flush()
    return run


async def mark_running(session: AsyncSession, run_id: uuid.UUID) -> None:
    await session.execute(
        update(ProcessingRun)
        .where(ProcessingRun.id == run_id)
        .values(status=RunStatus.RUNNING, started_at=func.now())
    )


async def mark_succeeded(session: AsyncSession, run_id: uuid.UUID) -> None:
    await session.execute(
        update(ProcessingRun)
        .where(ProcessingRun.id == run_id)
        .values(status=RunStatus.SUCCEEDED, finished_at=func.now(), error=None)
    )


async def mark_failed(session: AsyncSession, run_id: uuid.UUID, error: str) -> None:
    """Close a run as failed.

    By statement rather than by mutating an ORM object, for the same reason
    `documents.set_status` is: this runs straight after a rollback, which expires
    everything in the session, and an UPDATE does not care whether the identity
    map survived.
    """
    await session.execute(
        update(ProcessingRun)
        .where(ProcessingRun.id == run_id)
        .values(status=RunStatus.FAILED, finished_at=func.now(), error=error)
    )
