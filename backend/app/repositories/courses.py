"""Queries against `courses`. The only place course SQL is written."""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course


async def get(session: AsyncSession, course_id: uuid.UUID, user_id: uuid.UUID) -> Course | None:
    """Fetch one course. Scoped by `user_id` as well as id, always.

    The composite foreign key would catch a cross-tenant document anyway, but a
    lookup that ignores ownership hands the caller another user's row to make
    decisions about before the database ever sees an insert.
    """
    result = await session.execute(
        select(Course).where(Course.id == course_id, Course.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[Course]:
    """Every course this user owns, most recent term first.

    Ordered by `starts_on` descending because the upload form is the only caller
    and the course being uploaded to is almost always the current one. No
    pagination: a student has tens of courses, not thousands, and a limit nobody
    can page past is worse than no limit at all.
    """
    result = await session.execute(
        select(Course).where(Course.user_id == user_id).order_by(Course.starts_on.desc())
    )
    return list(result.scalars().all())


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    starts_on: date,
    ends_on: date,
    code: str | None = None,
    term: str | None = None,
) -> Course:
    course = Course(
        user_id=user_id,
        name=name,
        starts_on=starts_on,
        ends_on=ends_on,
        code=code,
        term=term,
    )
    session.add(course)
    # Populates the server-generated id without ending the caller's transaction.
    await session.flush()
    return course
