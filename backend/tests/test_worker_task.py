"""How the arq task classifies a failure.

This file exists because the classification was implemented wrong first and the
wrong version looked right. arq retries on `Retry`, `RetryJob` and
`CancelledError` and on nothing else -- so letting an exception propagate, which
reads like the obvious way to say "this failed, run it again", produces one
attempt and no retries. Every transient failure silently becomes permanent, and
the only visible symptom is a document that failed once for a reason that would
have cleared on its own.

Nothing here touches Postgres, Redis or storage: `process_document` is replaced,
because what is under test is the translation between an exception and arq's
retry protocol, not the processing.
"""

import uuid

import pytest
from arq.worker import Retry

from app.models.user import SEED_USER_ID
from app.services.errors import ServiceError
from app.workers import tasks
from app.workers.tasks import MAX_TRIES, process_document_task

COURSE_ID = uuid.uuid4()


def _ctx(job_try: int) -> dict:
    """The slice of arq's context the task reads."""
    return {"job_try": job_try}


async def _run(ctx: dict):
    return await process_document_task(
        ctx,
        user_id=str(SEED_USER_ID),
        course_id=str(COURSE_ID),
        storage_key=f"{SEED_USER_ID}/lecture.pdf",
        kind="lecture",
        title="Lecture",
    )


@pytest.fixture
def fails_with(monkeypatch: pytest.MonkeyPatch):
    """Replace `process_document` with one that raises what a test asks for."""

    def install(error: Exception):
        async def fake(*args, **kwargs):
            raise error

        monkeypatch.setattr(tasks, "process_document", fake)

    return install


async def test_a_service_error_is_permanent_and_asks_for_no_retry(fails_with) -> None:
    """`ServiceError` means the request cannot succeed as posed -- no text layer,
    no such course, a key with no object. Returning is what makes it permanent.

    Nothing is lost by swallowing it: `process_document` has already recorded the
    failure on the run row and moved the document to `failed`, and that is what
    the status endpoint reads. Redis only ever carried the request."""
    fails_with(ServiceError("could not read lecture.pdf as a PDF"))

    assert await _run(_ctx(1)) is None


async def test_a_transient_failure_asks_arq_to_retry(fails_with) -> None:
    fails_with(ConnectionError("connection reset"))

    with pytest.raises(Retry):
        await _run(_ctx(1))


@pytest.mark.parametrize("job_try", range(1, MAX_TRIES))
async def test_every_attempt_below_the_ceiling_retries(fails_with, job_try: int) -> None:
    """Off-by-one here is the difference between three attempts and two."""
    fails_with(ConnectionError("connection reset"))

    with pytest.raises(Retry):
        await _run(_ctx(job_try))


async def test_the_backoff_doubles(fails_with) -> None:
    """2s then 4s: long enough for a redeploy or a rate limit to clear, short
    enough that slice 4's polling UI does not look hung."""
    fails_with(ConnectionError("connection reset"))

    deferred = []
    for job_try in range(1, MAX_TRIES):
        with pytest.raises(Retry) as caught:
            await _run(_ctx(job_try))
        deferred.append(caught.value.defer_score)

    # `defer_score` is milliseconds -- arq converts the seconds handed to `Retry`.
    assert deferred == [2000, 4000]


async def test_the_last_attempt_raises_the_original_error(fails_with) -> None:
    """The final failure fails as itself rather than as another `Retry`.

    Two records then agree: Postgres has the failed run, and the worker log has
    the traceback. Asking for a retry that cannot be granted instead leaves arq
    to drop a phantom attempt with a `max retries exceeded` line and no exception
    to read."""
    fails_with(ConnectionError("connection reset"))

    with pytest.raises(ConnectionError):
        await _run(_ctx(MAX_TRIES))


async def test_the_worker_and_arq_agree_on_the_ceiling() -> None:
    """`WorkerSettings.max_tries` is arq's own bound and `MAX_TRIES` is the one
    the task enforces. If they drift, the higher one is silently ignored."""
    from app.workers.settings import WorkerSettings

    assert WorkerSettings.max_tries == MAX_TRIES
