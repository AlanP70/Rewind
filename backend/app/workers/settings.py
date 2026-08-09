"""What `arq app.workers.settings.WorkerSettings` runs.

Run locally in its own terminal, next to the API:

    uv run arq app.workers.settings.WorkerSettings

Not a Docker Compose service. The worker needs the same source tree and the same
`.env` the API has, so a container would duplicate both and then drift from them
-- and on the deploy it is a separate Render service reading the same repo, which
is what this mirrors.
"""

import logging
import sys

from arq.connections import RedisSettings

from app.core.config import settings
from app.core.db import engine
from app.workers.tasks import MAX_TRIES, process_document_task


async def startup(ctx: dict) -> None:
    """Give the `app` logger its own handler, so the worker says what it did.

    arq installs a handler on the `arq` logger and leaves the root logger with
    none. Raising `app` to INFO alone therefore changes nothing: the records
    propagate to a root with nothing attached and are dropped, so the worker
    reports that a job started and finished while saying nothing about pages,
    chunks, or what embedding is about to cost. Those lines existed only in the
    CLI until this hook.

    The handler goes on `app` rather than on the root via `basicConfig`, which is
    what the CLI does and what was tried first. A root handler also catches arq's
    own records on their way up, so every job line prints twice. Handling `app`
    where it is raised keeps the two sets of output separate, and `propagate` is
    then off so this stays true if anything else ever configures the root.
    """
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False


async def shutdown(ctx: dict) -> None:
    """Return the connection pool. The API does this in its lifespan; the worker
    is a separate process with its own pool and has to do it separately."""
    await engine.dispose()


class WorkerSettings:
    functions = [process_document_task]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown

    # Defined in `tasks.py`, because the task enforces it rather than arq: a
    # retry only happens when the task raises `Retry`, so the ceiling has to be
    # checked where that decision is made. Set here too so arq's own bound agrees
    # with ours instead of silently allowing more.
    max_tries = MAX_TRIES

    # Fifteen minutes. arq's default of five is sized for short jobs, and this
    # one is not: a long document is hundreds of chunks, each an OpenAI request,
    # and a timeout mid-embedding would be recorded as a failed attempt for a job
    # that was working correctly and merely slow. Embedding is resumable -- the
    # work list is "chunks where embedding IS NULL" -- so a retry after a genuine
    # hang resumes rather than re-billing, which makes a generous ceiling cheap.
    job_timeout = 900
