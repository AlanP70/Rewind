"""Layer (c) of the offset verification, and the only one that can fail for
reasons outside this process.

The property under test is invariant 3's real content: for every chunk,
`page_text[char_start:char_end]` is exactly the stored `content`. Layer (a)
proves the chunker is internally consistent, layer (b) proves it holds for a real
PDF at the moment of ingestion. Neither can see what Postgres actually kept, and
neither re-reads the file.

This does both: it re-downloads the PDF from storage, re-extracts it in a
separate process, and compares against what came back out of the database. That
is what makes it able to catch a text round-trip that lost or changed a
character, and extraction that is not reproducible across runs or across a
library upgrade.

Downloading rather than reading the file the caller happens to have is the same
argument as the separate process, one level out: the bytes in the bucket are what
the worker will chunk, so those are the bytes worth checking.
"""

import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import get_storage
from app.repositories import chunks as chunks_repo
from app.repositories import documents as documents_repo
from app.services.errors import ServiceError
from app.services.extraction import extract_pages_in_subprocess


def _describe_difference(expected: str, actual: str) -> str:
    """Locate the first divergence and quote a window around it.

    Printing both strings whole is useless at this size: a one-character change
    in a 900-character chunk is invisible in the dump, and the reader has to diff
    two walls of text by eye to find what the tool already knows.
    """
    offset = next(
        (i for i, (a, b) in enumerate(zip(expected, actual)) if a != b),
        min(len(expected), len(actual)),
    )
    window = 30
    low = max(0, offset - window)
    detail = (
        f"first difference at offset {offset}: "
        f"page {expected[low : offset + window]!r} != stored {actual[low : offset + window]!r}"
    )
    if len(expected) != len(actual):
        detail += f" (length {len(expected)} != {len(actual)})"
    return detail


@dataclass(frozen=True)
class ChunkFailure:
    chunk_index: int
    page_number: int
    reason: str


@dataclass(frozen=True)
class VerificationReport:
    document_id: uuid.UUID
    storage_key: str
    chunk_count: int
    pages_stored: int | None
    pages_extracted: int
    failures: list[ChunkFailure]

    @property
    def ok(self) -> bool:
        return not self.failures and self.pages_stored == self.pages_extracted


async def verify_document(
    session: AsyncSession, *, user_id: uuid.UUID, document_id: uuid.UUID
) -> VerificationReport:
    """Re-extract a document's PDF and check every chunk against it.

    Every chunk, not a sample: the whole document is nine chunks of a five page
    lecture, and a sample that happens to miss the broken one reports success.
    """
    document = await documents_repo.get(session, document_id, user_id)
    if document is None:
        raise ServiceError(f"no document {document_id} for this user")

    stored = await chunks_repo.list_for_document(session, document_id)
    if not stored:
        raise ServiceError(f"document {document_id} has no chunks to verify")

    # A key recorded in `documents` with no object behind it raises from here --
    # `download` reports the missing key itself, so there is no `exists` check to
    # race against.
    data = await get_storage().download(document.storage_key)
    pages = extract_pages_in_subprocess(
        data, name=PurePosixPath(document.storage_key).name
    )

    failures = []
    for chunk in stored:
        if chunk.page_number > len(pages):
            failures.append(
                ChunkFailure(
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    reason=f"page {chunk.page_number} does not exist; PDF has {len(pages)}",
                )
            )
            continue

        page_text = pages[chunk.page_number - 1]
        expected = page_text[chunk.char_start : chunk.char_end]
        # Compared as encoded bytes, not just as strings: two different sequences
        # of code points can render identically, and a normalisation difference
        # that survives a string comparison is exactly the kind of silent drift
        # this layer exists to catch.
        if expected.encode("utf-8") != chunk.content.encode("utf-8"):
            failures.append(
                ChunkFailure(
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    reason=(
                        f"[{chunk.char_start}:{chunk.char_end}] "
                        + _describe_difference(expected, chunk.content)
                    ),
                )
            )

    return VerificationReport(
        document_id=document_id,
        storage_key=document.storage_key,
        chunk_count=len(stored),
        pages_stored=document.page_count,
        pages_extracted=len(pages),
        failures=failures,
    )
