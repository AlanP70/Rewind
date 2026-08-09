"""The two document routes, against a real database with the queue stubbed.

The queue is a stub because what these routes owe the caller is a *decision*:
accept and enqueue exactly once, or refuse with a status code and enqueue
nothing. Running a real worker here would test arq -- which `test_worker_task.py`
already pins -- and would make the suite need Redis and an OpenAI key to answer a
question neither is involved in.

Where a test needs chunks to exist, it calls `process_document` directly. That is
the same function the worker calls, so the state under test is the state the
worker would have produced.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue, get_session
from app.core.config import settings
from app.core.storage import LocalStorage, get_storage, storage_key
from app.main import app
from app.models import DocumentStatus, ProcessingRun, RunStatus
from app.models.user import SEED_USER_ID
from app.repositories import documents as documents_repo
from app.repositories import processing_runs as runs_repo
from app.services.ingestion import create_course
from app.services.processing import process_document
from tests.conftest import CORRUPT_PDF, LECTURE


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class FakeQueue:
    """Records what would have been enqueued.

    `enqueue_job` keeps arq's signature -- function name positional, arguments by
    keyword -- so a test asserting on `jobs` is asserting on the call the real
    pool would have received.
    """

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, function: str, **kwargs) -> FakeJob:
        self.jobs.append((function, kwargs))
        return FakeJob(f"job-{len(self.jobs)}")


class BrokenQueue:
    """Redis unreachable. Raises rather than returning None, because that is what
    an actual dead connection does -- returning None is arq's "duplicate job id",
    a different and much rarer thing."""

    async def enqueue_job(self, function: str, **kwargs) -> FakeJob:
        raise ConnectionError("Error 111 connecting to redis:6379. Connection refused.")


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest_asyncio.fixture
async def client(session: AsyncSession, queue: FakeQueue) -> AsyncIterator[AsyncClient]:
    """The app with its two dependencies redirected at the test fixtures.

    `ASGITransport` does not run the lifespan, so the real arq pool is never
    opened and `app.state.queue` never exists -- overriding `get_queue` is what
    makes that safe rather than a latent AttributeError.
    """
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_queue] = lambda: queue

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def course_id(session: AsyncSession) -> uuid.UUID:
    course = await create_course(
        session,
        user_id=SEED_USER_ID,
        name="Algorithms",
        starts_on=date(2024, 9, 1),
        ends_on=date(2024, 12, 15),
    )
    await session.commit()
    return course.id


def _upload_form(course_id: uuid.UUID, data: bytes = b"", **fields) -> dict:
    """A multipart body. `embed` is off unless a test says otherwise -- no test
    here has any business spending money on OpenAI."""
    return {
        "data": {"course_id": str(course_id), "embed": "false", **fields},
        "files": {"file": ("Lecture.pdf", data or LECTURE.read_bytes(), "application/pdf")},
    }


async def _process(session: AsyncSession, course_id: uuid.UUID, key: str) -> None:
    await process_document(
        session,
        user_id=SEED_USER_ID,
        course_id=course_id,
        storage_key=key,
        kind="lecture",
        title="Lecture",
        embed=False,
    )


async def test_upload_is_accepted_and_enqueued_once(
    client: AsyncClient, queue: FakeQueue, course_id: uuid.UUID
) -> None:
    response = await client.post("/documents", **_upload_form(course_id))

    assert response.status_code == 202
    body = response.json()
    assert body["reused_document"] is False
    assert body["job_id"] == "job-1"

    assert len(queue.jobs) == 1
    function, kwargs = queue.jobs[0]
    assert function == "process_document_task"
    assert kwargs["storage_key"] == storage_key(SEED_USER_ID, "Lecture.pdf")
    assert kwargs["embed"] is False


async def test_upload_stores_the_row_and_the_bytes_before_it_answers(
    client: AsyncClient, session: AsyncSession, course_id: uuid.UUID
) -> None:
    """The ordering `submit_document`'s docstring commits to.

    A worker can pick the job up the instant it is enqueued, so if either the row
    or the object were still uncommitted at that point it would be handed a
    document it cannot see.
    """
    response = await client.post("/documents", **_upload_form(course_id))
    document_id = uuid.UUID(response.json()["document_id"])

    document = await documents_repo.get(session, document_id, SEED_USER_ID)
    assert document is not None
    assert document.status == DocumentStatus.PENDING

    stored = await get_storage().download(document.storage_key)
    assert stored == LECTURE.read_bytes()


async def test_an_empty_file_is_rejected_before_anything_is_stored(
    client: AsyncClient, queue: FakeQueue, course_id: uuid.UUID
) -> None:
    response = await client.post(
        "/documents",
        data={"course_id": str(course_id), "embed": "false"},
        files={"file": ("Empty.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]
    assert queue.jobs == []


async def test_an_unknown_course_is_404_and_leaves_no_trace(
    client: AsyncClient, session: AsyncSession, queue: FakeQueue
) -> None:
    """A precondition failure enqueues nothing, stores nothing and records no run.

    This is the HTTP face of the rule `processing_runs` was designed around: a
    request that was never valid leaves no history of an attempt, because there
    was no attempt.
    """
    missing = uuid.uuid4()
    response = await client.post("/documents", **_upload_form(missing))

    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]
    assert queue.jobs == []

    key = storage_key(SEED_USER_ID, "Lecture.pdf")
    assert not (LocalStorage(settings.storage_root).root / key).exists()


async def test_re_upload_without_force_is_409_and_enqueues_nothing(
    client: AsyncClient, session: AsyncSession, queue: FakeQueue, course_id: uuid.UUID
) -> None:
    await client.post("/documents", **_upload_form(course_id))
    await _process(session, course_id, storage_key(SEED_USER_ID, "Lecture.pdf"))

    response = await client.post("/documents", **_upload_form(course_id))

    assert response.status_code == 409
    # The message has to tell an HTTP client what to do, not a CLI user.
    assert "force" in response.json()["detail"]
    assert len(queue.jobs) == 1


async def test_force_re_upload_reuses_the_same_document(
    client: AsyncClient, session: AsyncSession, course_id: uuid.UUID
) -> None:
    first = await client.post("/documents", **_upload_form(course_id))
    await _process(session, course_id, storage_key(SEED_USER_ID, "Lecture.pdf"))

    second = await client.post("/documents", **_upload_form(course_id, force="true"))

    assert second.status_code == 202
    assert second.json()["reused_document"] is True
    assert second.json()["document_id"] == first.json()["document_id"]


async def test_status_before_a_worker_has_touched_it(
    client: AsyncClient, course_id: uuid.UUID
) -> None:
    """Queued but not picked up: the run exists and says `queued`.

    This test previously asserted the run fields were *null* here, which encoded
    the bug rather than the requirement -- the document was accepted with no
    record that any work was owed. The upload now opens the run before it answers,
    so a job Redis loses is still visible as an outstanding attempt.
    """
    document_id = (await client.post("/documents", **_upload_form(course_id))).json()[
        "document_id"
    ]

    body = (await client.get(f"/documents/{document_id}/status")).json()

    assert body["status"] == DocumentStatus.PENDING
    assert body["chunks_total"] == 0
    assert body["chunks_embedded"] == 0
    assert body["attempts"] == 1
    assert body["run_status"] == RunStatus.QUEUED
    assert body["error"] is None
    # Only just enqueued, so not yet suspicious.
    assert body["stale"] is False


async def test_a_lost_job_is_visible_as_an_outstanding_queued_run(
    client: AsyncClient, session: AsyncSession, course_id: uuid.UUID
) -> None:
    """The whole reason the queued row exists.

    Simulates Redis dropping the job -- the run is never claimed -- by backdating
    it past the threshold. Before this fix the same situation reported `pending`
    with every run field null and `stale: false`, which is indistinguishable from
    a document uploaded a second ago. That silence is what the free Key Value
    tier's lack of persistence would have produced in production.
    """
    document_id = uuid.UUID(
        (await client.post("/documents", **_upload_form(course_id))).json()["document_id"]
    )

    forgotten = datetime.now(UTC) - timedelta(
        seconds=settings.stale_run_after_seconds + 60
    )
    await session.execute(
        update(ProcessingRun)
        .where(ProcessingRun.document_id == document_id)
        .values(created_at=forgotten)
    )
    await session.commit()

    body = (await client.get(f"/documents/{document_id}/status")).json()

    assert body["run_status"] == RunStatus.QUEUED
    assert body["stale"] is True
    assert body["attempts"] == 1


async def test_processing_claims_the_queued_run_instead_of_opening_another(
    client: AsyncClient, session: AsyncSession, course_id: uuid.UUID
) -> None:
    """One upload is one attempt, however many rows it took to get there.

    If the worker opened its own run the upload would be attempt 1 and its
    execution attempt 2, and the queued row would sit there forever claiming work
    was still owed on a document that had finished.
    """
    document_id = uuid.UUID(
        (await client.post("/documents", **_upload_form(course_id))).json()["document_id"]
    )

    await _process(session, course_id, storage_key(SEED_USER_ID, "Lecture.pdf"))

    runs = await runs_repo.list_for_document(session, document_id)
    assert [(run.attempts, run.status) for run in runs] == [
        (1, RunStatus.SUCCEEDED)
    ]
    assert runs[0].started_at is not None


async def test_a_failed_enqueue_closes_the_run_rather_than_leaving_it_owed(
    client: AsyncClient, session: AsyncSession, queue: FakeQueue, course_id: uuid.UUID
) -> None:
    """A dispatch that never happened is not outstanding work.

    Left at `queued` it would read as a job in flight forever, and the repair --
    re-uploading -- would open a second queued row beside the first.
    """
    app.dependency_overrides[get_queue] = lambda: BrokenQueue()
    try:
        with pytest.raises(ConnectionError):
            await client.post("/documents", **_upload_form(course_id))
    finally:
        app.dependency_overrides[get_queue] = lambda: queue

    document = await documents_repo.get_by_storage_key(
        session, SEED_USER_ID, storage_key(SEED_USER_ID, "Lecture.pdf")
    )
    assert document is not None
    runs = await runs_repo.list_for_document(session, document.id)
    assert [run.status for run in runs] == [RunStatus.FAILED]
    assert "could not enqueue" in runs[0].error


async def test_status_reports_honest_counts_after_chunking(
    client: AsyncClient, session: AsyncSession, course_id: uuid.UUID
) -> None:
    """Chunked but not embedded. `chunks_embedded` is 0 against a real
    `chunks_total`, and the document is still `processing` -- it has text but
    cannot be searched, which is what `ready` means."""
    document_id = (await client.post("/documents", **_upload_form(course_id))).json()[
        "document_id"
    ]
    await _process(session, course_id, storage_key(SEED_USER_ID, "Lecture.pdf"))

    body = (await client.get(f"/documents/{document_id}/status")).json()

    assert body["status"] == DocumentStatus.PROCESSING
    assert body["chunks_total"] > 0
    assert body["chunks_embedded"] == 0
    assert body["attempts"] == 1
    assert body["run_status"] == RunStatus.SUCCEEDED
    assert body["stale"] is False


async def test_a_failed_document_is_200_with_a_readable_error(
    client: AsyncClient, session: AsyncSession, course_id: uuid.UUID
) -> None:
    """The phase's headline requirement, from the API side.

    200 rather than 4xx: the request to *know* succeeded. A status code that made
    a client's error handling fire on a perfectly good answer would hide the
    error it was reporting.
    """
    response = await client.post(
        "/documents", **_upload_form(course_id, data=CORRUPT_PDF)
    )
    document_id = response.json()["document_id"]

    with pytest.raises(Exception):
        await _process(session, course_id, storage_key(SEED_USER_ID, "Lecture.pdf"))

    status_response = await client.get(f"/documents/{document_id}/status")

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == DocumentStatus.FAILED
    assert body["run_status"] == RunStatus.FAILED
    assert body["error"]
    assert body["attempts"] == 1


async def test_status_for_an_unknown_document_is_404(client: AsyncClient) -> None:
    missing = uuid.uuid4()
    response = await client.get(f"/documents/{missing}/status")

    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]


async def test_a_running_run_past_the_threshold_is_stale(
    client: AsyncClient, session: AsyncSession, course_id: uuid.UUID
) -> None:
    """Crash *detection*: a run still claiming `running` after arq would have
    cancelled it has no one left to finish it.

    `started_at` is backdated by a direct UPDATE because no service can produce
    this state -- that is the point of it. It is what a hard-killed worker leaves
    behind.
    """
    document_id = uuid.UUID(
        (await client.post("/documents", **_upload_form(course_id))).json()["document_id"]
    )
    # Claimed rather than inserted: the upload already opened attempt 1 at
    # `queued`, and a worker taking it is exactly how a run reaches `running`.
    run = await runs_repo.claim_queued(session, document_id)
    assert run is not None
    dead = datetime.now(UTC) - timedelta(seconds=settings.stale_run_after_seconds + 60)
    await session.execute(
        update(ProcessingRun).where(ProcessingRun.id == run.id).values(started_at=dead)
    )
    await session.commit()

    body = (await client.get(f"/documents/{document_id}/status")).json()

    assert body["run_status"] == RunStatus.RUNNING
    assert body["stale"] is True


async def test_a_run_within_the_threshold_is_not_stale(
    client: AsyncClient, session: AsyncSession, course_id: uuid.UUID
) -> None:
    """The other half of the pair. Without it, `stale = True` would pass."""
    document_id = uuid.UUID(
        (await client.post("/documents", **_upload_form(course_id))).json()["document_id"]
    )
    assert await runs_repo.claim_queued(session, document_id) is not None
    await session.commit()

    body = (await client.get(f"/documents/{document_id}/status")).json()

    assert body["stale"] is False
