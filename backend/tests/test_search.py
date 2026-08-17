"""Vector search, against a real database and hand-built vectors.

No OpenAI key and no PDFs. Embeddings here are unit vectors along three axes,
which makes the expected ranking arithmetic rather than a judgement: cosine
distance to `[1, 0, 0, ...]` is 0 for itself, 1 for anything orthogonal. That is
the only way to test *ordering* separately from retrieval quality -- whether the
right passage comes back for a real question is slice 4's eval, and it is a
measurement, not an assertion.

What is worth pinning here is everything a wrong query would get wrong silently:
which rows are eligible, whose rows they are, and the direction of the sort.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.main import app
from app.models import Chunk, Course, Document, DocumentStatus, OccurredAtSource, User
from app.models.chunk import EMBEDDING_DIMENSIONS
from app.models.user import SEED_USER_ID
from app.repositories.search import count_embedded_chunks, nearest_chunks
from app.services.errors import ServiceError
from app.services.ingestion import create_course
from app.services.search import search

# A second owner, for the isolation test below. `users` is deliberately not in
# conftest's TRUNCATE list -- the seed user is inserted by migration 0002 and has
# to survive -- so this row outlives the test that creates it. A future test
# needing a second user should reuse this id rather than insert its own, which
# would fail on the primary key.
OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


def axis(index: int) -> list[float]:
    """A unit vector along one axis. Two different axes are orthogonal, so their
    cosine distance is exactly 1 and no ranking depends on floating-point luck."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    return vector


async def make_document(
    session: AsyncSession,
    *,
    course: Course,
    title: str,
    vectors: list[list[float] | None],
    user_id: uuid.UUID = SEED_USER_ID,
    occurred_at: datetime | None = None,
) -> Document:
    """One document with one chunk per vector. `None` means unembedded."""
    document = Document(
        user_id=user_id,
        course_id=course.id,
        title=title,
        kind="lecture",
        storage_key=f"{user_id}/{title}.pdf",
        status=DocumentStatus.READY,
        occurred_at=occurred_at,
        occurred_at_source=OccurredAtSource.MANUAL if occurred_at else None,
    )
    session.add(document)
    await session.flush()

    for index, vector in enumerate(vectors):
        session.add(
            Chunk(
                user_id=user_id,
                document_id=document.id,
                content=f"{title} chunk {index}",
                embedding=vector,
                page_number=index + 1,
                char_start=0,
                char_end=10,
                chunk_index=index,
            )
        )
    await session.flush()
    return document


@pytest_asyncio.fixture
async def course(session: AsyncSession) -> Course:
    return await create_course(
        session,
        user_id=SEED_USER_ID,
        name="Introduction to Algorithms",
        starts_on=date(2020, 2, 3),
        ends_on=date(2020, 5, 12),
    )


@pytest.mark.asyncio
async def test_hits_come_back_nearest_first(session: AsyncSession, course: Course) -> None:
    await make_document(session, course=course, title="near", vectors=[axis(0)])
    await make_document(session, course=course, title="far", vectors=[axis(1)])

    hits = await nearest_chunks(
        session, user_id=SEED_USER_ID, embedding=axis(0), limit=10
    )

    # The whole point of the sort. An ORDER BY with the wrong direction returns
    # the same rows, the same count, and the worst match first -- and a test that
    # only asserted "two hits" would pass.
    assert [hit.document_title for hit in hits] == ["near", "far"]
    assert hits[0].distance == pytest.approx(0.0)
    assert hits[1].distance == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_unembedded_chunks_are_not_ranked(
    session: AsyncSession, course: Course
) -> None:
    """A chunk with no vector is a document still being processed.

    pgvector sorts a null distance last rather than dropping the row, so without
    an explicit filter these arrive at the end of a result set that is not full
    -- present, unranked, and indistinguishable from a poor match.
    """
    await make_document(session, course=course, title="embedded", vectors=[axis(0)])
    await make_document(session, course=course, title="pending", vectors=[None, None])

    hits = await nearest_chunks(
        session, user_id=SEED_USER_ID, embedding=axis(0), limit=10
    )

    assert [hit.document_title for hit in hits] == ["embedded"]


@pytest.mark.asyncio
async def test_another_users_chunks_are_never_returned(
    session: AsyncSession, course: Course
) -> None:
    """Auth is Phase 7; the scoping is here now.

    The other user's chunk is the *closer* match, so a query missing its
    `user_id` filter does not merely leak -- it ranks the leak first.

    Setting this up needs a second user *and* a second course, because
    `fk_documents_course_id_user_id` refuses to hang one user's document off
    another user's course. That constraint is a real part of the answer here: it
    is why the leak this test guards against has to be manufactured deliberately.
    """
    session.add(User(id=OTHER_USER_ID, email="someone.else@example.com"))
    await session.flush()
    theirs = await create_course(
        session,
        user_id=OTHER_USER_ID,
        name="Their Algorithms Course",
        starts_on=date(2020, 2, 3),
        ends_on=date(2020, 5, 12),
    )

    await make_document(session, course=course, title="mine", vectors=[axis(1)])
    await make_document(
        session, course=theirs, title="theirs", vectors=[axis(0)], user_id=OTHER_USER_ID
    )

    hits = await nearest_chunks(
        session, user_id=SEED_USER_ID, embedding=axis(0), limit=10
    )

    assert [hit.document_title for hit in hits] == ["mine"]


@pytest.mark.asyncio
async def test_course_scoping_is_optional_and_cross_course_by_default(
    session: AsyncSession, course: Course
) -> None:
    other = await create_course(
        session,
        user_id=SEED_USER_ID,
        name="Design and Analysis of Algorithms",
        starts_on=date(2021, 2, 1),
        ends_on=date(2021, 5, 15),
    )
    await make_document(session, course=course, title="6.006", vectors=[axis(0)])
    await make_document(session, course=other, title="6.046", vectors=[axis(0)])

    everywhere = await nearest_chunks(
        session, user_id=SEED_USER_ID, embedding=axis(0), limit=10
    )
    scoped = await nearest_chunks(
        session, user_id=SEED_USER_ID, embedding=axis(0), limit=10, course_id=other.id
    )

    assert {hit.document_title for hit in everywhere} == {"6.006", "6.046"}
    assert [hit.document_title for hit in scoped] == ["6.046"]


@pytest.mark.asyncio
async def test_hits_carry_the_documents_date_and_its_source(
    session: AsyncSession, course: Course
) -> None:
    """Denormalised onto the hit so the timeline can order and label without a
    second request -- and so an undated hit is visibly undated rather than
    missing."""
    dated = datetime(2020, 3, 10, tzinfo=UTC)
    await make_document(
        session, course=course, title="dated", vectors=[axis(0)], occurred_at=dated
    )
    await make_document(session, course=course, title="undated", vectors=[axis(1)])

    hits = await nearest_chunks(
        session, user_id=SEED_USER_ID, embedding=axis(0), limit=10
    )

    assert hits[0].occurred_at == dated
    assert hits[0].occurred_at_source == OccurredAtSource.MANUAL
    assert hits[1].occurred_at is None
    assert hits[1].occurred_at_source is None


@pytest.mark.asyncio
async def test_corpus_size_counts_only_embedded_chunks(
    session: AsyncSession, course: Course
) -> None:
    """The denominator of every latency number, so it counts what was searched."""
    await make_document(session, course=course, title="ready", vectors=[axis(0), axis(1)])
    await make_document(session, course=course, title="pending", vectors=[None])

    size = await count_embedded_chunks(session, user_id=SEED_USER_ID)

    assert size.embedded_chunks == 2
    assert size.documents == 1


@pytest.mark.asyncio
async def test_an_empty_query_is_refused_before_it_is_paid_for(
    session: AsyncSession,
) -> None:
    """Whitespace, specifically. `min_length=1` on the schema stops `""` at the
    route; only the service stops `"   "`, and reaching OpenAI with it would cost
    a request to embed nothing."""
    with pytest.raises(ServiceError, match="needs a query"):
        await search(session, user_id=SEED_USER_ID, query="   ")


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_route_refuses_an_empty_query_without_calling_openai(
    client: AsyncClient,
) -> None:
    """422 from the schema, not 400 from the service, and no key needed either
    way -- the request never reaches the embedding call."""
    response = await client.post("/search", json={"query": ""})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_route_rejects_a_limit_beyond_the_ceiling(client: AsyncClient) -> None:
    response = await client.post("/search", json={"query": "recursion", "limit": 500})

    assert response.status_code == 422
