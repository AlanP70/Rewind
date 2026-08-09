"""List the courses a document can be uploaded to.

Exists because `POST /documents` takes a `course_id` and the upload form has no
other way to learn one. Read-only: courses are created by the CLI, which is where
term bounds get entered deliberately rather than typed into a form field.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.models.user import SEED_USER_ID
from app.schemas.courses import CourseSummary
from app.services.ingestion import list_courses

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("")
async def read_courses(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CourseSummary]:
    """No 404 for an empty list. Owning no courses is a legitimate answer, and an
    empty array is what lets the form say so."""
    return await list_courses(session, user_id=SEED_USER_ID)
