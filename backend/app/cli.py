"""The ingestion CLI: `python -m app.cli`.

Phase 1 runs ingestion synchronously here. There is no queue and no UI; the
worker and the upload endpoint are Phase 2, and when they arrive they call the
same services this does.
"""

import argparse
import asyncio
import statistics
import sys
import uuid
from datetime import date
from pathlib import Path

from app.core.db import async_session
from app.models.user import SEED_USER_ID
from app.schemas.ingestion import PlannedChunk
from app.services.extraction import extract_pages
from app.services.ingestion import IngestError, create_course, ingest_document, plan_chunks


def _check_offsets(pages: list[str], planned: list[PlannedChunk]) -> list[str]:
    """Layer (b): assert every chunk's offsets slice its page back to its content.

    In-process, so it cannot catch non-deterministic extraction or corruption on
    the way through Postgres -- that is what `verify` is for. What it does catch
    is the chunker and `plan_chunks` disagreeing about a real PDF's text, which
    synthetic strings in the property test may not provoke.
    """
    failures = []
    for chunk in planned:
        expected = pages[chunk.page_number - 1][chunk.char_start : chunk.char_end]
        if expected != chunk.content:
            failures.append(
                f"chunk {chunk.chunk_index} (page {chunk.page_number}, "
                f"{chunk.char_start}:{chunk.char_end}): "
                f"page text {expected!r} != content {chunk.content!r}"
            )
    return failures


def _report(pages: list[str], planned: list[PlannedChunk]) -> None:
    sizes = [chunk.char_end - chunk.char_start for chunk in planned]
    print(f"pages: {len(pages)}")
    print(f"chunks: {len(planned)}")

    for page_offset, text in enumerate(pages):
        on_page = [c for c in planned if c.page_number == page_offset + 1]
        print(f"  page {page_offset + 1}: {len(text)} chars -> {len(on_page)} chunks")

    if sizes:
        median = int(statistics.median(sizes))
        print(f"chunk size: min {min(sizes)}, median {median}, max {max(sizes)}")


async def _create_course(args: argparse.Namespace) -> int:
    async with async_session() as session, session.begin():
        course = await create_course(
            session,
            user_id=SEED_USER_ID,
            name=args.name,
            starts_on=args.starts_on,
            ends_on=args.ends_on,
            code=args.code,
            term=args.term,
        )
        print(f"course {course.id}  {course.name}  {course.starts_on}..{course.ends_on}")
    return 0


async def _ingest(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 1

    if args.dry_run:
        pages = extract_pages(path)
        planned = plan_chunks(pages)
        _report(pages, planned)

        failures = _check_offsets(pages, planned)
        if failures:
            print(f"\nFAILED: {len(failures)} chunk(s) do not slice back:", file=sys.stderr)
            for failure in failures[:10]:
                print(f"  {failure}", file=sys.stderr)
            return 1

        print(f"\nOK: all {len(planned)} chunks slice back to their page text. Nothing written.")
        return 0

    # One transaction for the whole document: a crash between deleting old chunks
    # and writing new ones must not leave a document with none.
    async with async_session() as session, session.begin():
        result = await ingest_document(
            session,
            user_id=SEED_USER_ID,
            course_id=args.course_id,
            path=path,
            kind=args.kind,
            title=args.title or path.stem,
            force=args.force,
        )

    verb = "re-ingested" if result.reused_document else "ingested"
    replaced = f" (replaced {result.replaced_chunks})" if result.replaced_chunks else ""
    print(f"{verb} document {result.document_id}")
    print(f"  pages: {result.page_count}")
    print(f"  chunks: {result.chunk_count}{replaced}")
    # Plain hyphen, not an em dash: the Windows console is cp1252 and mangles it.
    print("  status: processing - embeddings not generated yet")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subcommands = parser.add_subparsers(dest="command", required=True)

    course = subcommands.add_parser("create-course", help="create a course to ingest into")
    course.add_argument("name")
    # ISO dates only. Ambiguous formats are how a term silently lands in the
    # wrong year, and Phase 3 dates every lecture by interpolating within it.
    course.add_argument("--starts-on", type=date.fromisoformat, required=True)
    course.add_argument("--ends-on", type=date.fromisoformat, required=True)
    course.add_argument("--code")
    course.add_argument("--term")
    course.set_defaults(handler=_create_course)

    ingest = subcommands.add_parser("ingest", help="ingest a PDF into a course")
    ingest.add_argument("course_id", type=uuid.UUID)
    ingest.add_argument("path")
    ingest.add_argument("--kind", default="lecture", choices=["lecture", "assignment", "note", "syllabus"])
    ingest.add_argument("--title", help="defaults to the filename without its extension")
    ingest.add_argument(
        "--force", action="store_true", help="replace existing chunks for this document"
    )
    ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="extract, chunk and check offsets without writing anything",
    )
    ingest.set_defaults(handler=_ingest)

    args = parser.parse_args()
    try:
        return asyncio.run(args.handler(args))
    except IngestError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
