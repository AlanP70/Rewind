"""Ingestion: a PDF path in, rows in `documents` and `chunks` out.

This module owns the one place content is produced. `plan_chunks` slices each
page string with the offsets `chunk_page` returned, so a `PlannedChunk`'s
`content` and its `(char_start, char_end)` cannot disagree -- there is no other
code path that builds content.

Nothing here commits. The caller owns the transaction, which is what lets the CLI
and (from Phase 2) the arq worker call the same function.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.paths import to_storage_path
from app.models import Course, DocumentStatus
from app.repositories import chunks as chunks_repo
from app.repositories import courses as courses_repo
from app.repositories import documents as documents_repo
from app.schemas.ingestion import PlannedChunk
from app.services.chunking import chunk_page
from app.services.errors import ServiceError
from app.services.extraction import extract_pages


@dataclass(frozen=True)
class IngestResult:
    document_id: uuid.UUID
    page_count: int
    chunk_count: int
    replaced_chunks: int
    reused_document: bool


def plan_chunks(pages: list[str]) -> list[PlannedChunk]:
    """Chunk every page, in document order.

    Empty pages contribute nothing but do not disturb numbering: page numbers
    come from the list index, so an image-only slide leaves a gap in the chunk
    record without shifting the pages after it.
    """
    planned: list[PlannedChunk] = []
    chunk_index = 0

    for page_offset, text in enumerate(pages):
        for char_start, char_end in chunk_page(text):
            planned.append(
                PlannedChunk(
                    page_number=page_offset + 1,
                    chunk_index=chunk_index,
                    char_start=char_start,
                    char_end=char_end,
                    # The only place content is ever produced.
                    content=text[char_start:char_end],
                )
            )
            chunk_index += 1

    return planned


async def create_course(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    starts_on: date,
    ends_on: date,
    code: str | None = None,
    term: str | None = None,
) -> Course:
    """Create a course. Term bounds are real data: Phase 3 dates lectures by
    interpolating within them, so a backwards term would silently produce
    nonsense dates rather than an error."""
    if ends_on < starts_on:
        raise ServiceError(f"course ends_on {ends_on} is before starts_on {starts_on}")

    return await courses_repo.create(
        session,
        user_id=user_id,
        name=name,
        starts_on=starts_on,
        ends_on=ends_on,
        code=code,
        term=term,
    )


async def ingest_document(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    path: Path,
    kind: str,
    title: str,
    force: bool = False,
) -> IngestResult:
    """Extract, chunk and store one PDF.

    Re-ingesting the same path is the same document, not a second one -- that is
    what `UNIQUE (user_id, storage_path)` encodes. A re-ingest that would destroy
    existing chunks refuses unless `force` is set, because silently rebuilding is
    indistinguishable from silently doubling when it goes wrong.

    `occurred_at` is left null. Phase 1 has no date inference and will not invent
    one; from Phase 3 only `redate_document` writes that column.
    """
    course = await courses_repo.get(session, course_id, user_id)
    if course is None:
        raise ServiceError(f"no course {course_id} for this user")

    # Canonical and repo-relative, so the same file reached by two different
    # relative paths is one document, and the row still means something on a
    # machine that is not this one.
    try:
        storage_path = to_storage_path(path)
    except ValueError as error:
        raise ServiceError(str(error)) from error

    document = await documents_repo.get_by_storage_path(session, user_id, storage_path)
    reused = document is not None
    replaced = 0

    if document is not None:
        if document.course_id != course_id:
            raise ServiceError(
                f"{storage_path} is already ingested under course {document.course_id}"
            )
        existing = await chunks_repo.count_for_document(session, document.id)
        if existing and not force:
            raise ServiceError(
                f"{storage_path} already has {existing} chunks; pass --force to replace them"
            )
        replaced = await chunks_repo.delete_for_document(session, document.id)
    else:
        document = await documents_repo.create(
            session,
            user_id=user_id,
            course_id=course_id,
            kind=kind,
            title=title,
            storage_path=storage_path,
        )

    pages = extract_pages(path)
    planned = plan_chunks(pages)
    if not planned:
        raise ServiceError(f"{path} yielded no text; is it a scan with no text layer?")

    await chunks_repo.insert_many(
        session, user_id=user_id, document_id=document.id, planned=planned
    )

    document.page_count = len(pages)
    # Not `ready`: a document with no embeddings cannot be searched, and calling
    # it ready would make Phase 4 look broken rather than incomplete. The embed
    # step is what advances it.
    document.status = DocumentStatus.PROCESSING

    return IngestResult(
        document_id=document.id,
        page_count=len(pages),
        chunk_count=len(planned),
        replaced_chunks=replaced,
        reused_document=reused,
    )
