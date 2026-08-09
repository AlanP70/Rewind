"""`GET /courses` — what the upload form's picker reads."""

from collections.abc import AsyncIterator
from datetime import date

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.main import app
from app.models.user import SEED_USER_ID
from app.services.ingestion import create_course


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def test_no_courses_is_an_empty_list_not_a_404(client: AsyncClient) -> None:
    """Owning no courses is a legitimate answer. A 404 would make the form's
    first-run state indistinguishable from a broken route."""
    response = await client.get("/courses")

    assert response.status_code == 200
    assert response.json() == []


async def test_courses_come_back_most_recent_term_first(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The picker's default should be the course being taken now, which is the
    only reason this endpoint has an order at all."""
    for name, year in [("Older", 2023), ("Newest", 2025), ("Middle", 2024)]:
        await create_course(
            session,
            user_id=SEED_USER_ID,
            name=name,
            starts_on=date(year, 9, 1),
            ends_on=date(year, 12, 15),
        )
    await session.commit()

    body = (await client.get("/courses")).json()

    assert [course["name"] for course in body] == ["Newest", "Middle", "Older"]


async def test_a_course_carries_what_the_picker_needs_to_disambiguate(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Name alone does not distinguish the same course taken twice."""
    await create_course(
        session,
        user_id=SEED_USER_ID,
        name="Algorithms",
        starts_on=date(2024, 9, 1),
        ends_on=date(2024, 12, 15),
        code="CS161",
        term="Fall 2024",
    )
    await session.commit()

    course = (await client.get("/courses")).json()[0]

    assert course["name"] == "Algorithms"
    assert course["code"] == "CS161"
    assert course["term"] == "Fall 2024"
    assert course["starts_on"] == "2024-09-01"
    assert course["ends_on"] == "2024-12-15"
