"""Ingestion: a PDF's bytes in, rows in `documents` and `chunks` out.

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
from pathlib import PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, Document, DocumentStatus
from app.repositories import chunks as chunks_repo
from app.repositories import courses as courses_repo
from app.repositories import documents as documents_repo
from app.schemas.ingestion import PlannedChunk
from app.services.chunking import chunk_page
from app.services.errors import ConflictError, NotFoundError, ServiceError
from app.services.extraction import extract_pages


@dataclass(frozen=True)
class ResolvedDocument:
    """A document row that exists and is cleared for (re)processing."""

    document: Document
    reused: bool
    existing_chunks: int


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


async def resolve_document(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    storage_key: str,
    kind: str,
    title: str,
    force: bool = False,
) -> ResolvedDocument:
    """Find or create the document row, and decide whether processing may proceed.

    Split from `ingest_document` so a `processing_runs` row can be opened between
    the two: a run points at a document, so the document has to exist before the
    expensive, failure-prone part starts. Otherwise a PDF that fails to parse
    leaves no processing history at all, which is the one case the history exists
    for.

    Everything here is a precondition about the *request* -- unknown course,
    existing chunks without `force`. None of it is a processing failure, so none
    of it produces a run.

    Uploading is the caller's job, not this function's -- the worker only ever
    receives a key and never saw the bytes, so the key is what identifies a
    document here. The two callers upload on opposite sides of this call and both
    are right: the CLI uploads first, because `process_document` resolves
    internally and there is no earlier hook; `submit_document` resolves first,
    because it has one, and a request rejected here should not leave an object
    behind. Nothing in this function reads the object either way.

    Re-ingesting the same key is the same document, not a second one -- that is
    what `UNIQUE (user_id, storage_key)` encodes. A re-ingest that would destroy
    existing chunks refuses unless `force` is set, because silently rebuilding is
    indistinguishable from silently doubling when it goes wrong.

    `occurred_at` is left null. Phase 1 has no date inference and will not invent
    one; from Phase 3 only `redate_document` writes that column.
    """
    course = await courses_repo.get(session, course_id, user_id)
    if course is None:
        raise NotFoundError(f"no course {course_id} for this user")

    document = await documents_repo.get_by_storage_key(session, user_id, storage_key)

    if document is None:
        document = await documents_repo.create(
            session,
            user_id=user_id,
            course_id=course_id,
            kind=kind,
            title=title,
            storage_key=storage_key,
        )
        return ResolvedDocument(document=document, reused=False, existing_chunks=0)

    if document.course_id != course_id:
        raise ConflictError(
            f"{storage_key} is already ingested under course {document.course_id}"
        )

    existing = await chunks_repo.count_for_document(session, document.id)
    if existing and not force:
        # Worded without naming a flag. This message reaches two callers who spell
        # the same decision differently -- `--force` on the CLI, `force=true` in
        # the form -- and telling an HTTP client to "pass --force" sends it looking
        # for something that does not exist.
        raise ConflictError(
            f"{storage_key} already has {existing} chunks; set force to replace them"
        )

    return ResolvedDocument(document=document, reused=True, existing_chunks=existing)


async def ingest_document(
    session: AsyncSession, *, resolved: ResolvedDocument, data: bytes
) -> IngestResult:
    """Extract, chunk and store one PDF into an already-resolved document.

    Takes the bytes the caller downloaded rather than fetching them itself, so
    the download happens once per attempt and this function stays a pure
    transformation of bytes into rows.

    Always replaces: the `--force` guard was answered in `resolve_document`, so by
    the time execution is here, rebuilding is what was asked for. That is also
    what makes a retry safe -- attempt 2 clears attempt 1's partial chunks rather
    than colliding with them.
    """
    document = resolved.document
    replaced = await chunks_repo.delete_for_document(session, document.id)

    # The key's filename alone. The `{user_id}/` prefix is noise in an error
    # message a person has to read.
    name = PurePosixPath(document.storage_key).name

    pages = extract_pages(data, name=name)
    planned = plan_chunks(pages)
    if not planned:
        raise ServiceError(f"{name} yielded no text; is it a scan with no text layer?")

    await chunks_repo.insert_many(
        session, user_id=document.user_id, document_id=document.id, planned=planned
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
        reused_document=resolved.reused,
    )
