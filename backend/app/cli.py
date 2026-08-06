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
from app.services.embedding import embed_document, estimate, pending_chunks
from app.services.errors import ServiceError
from app.services.extraction import extract_pages
from app.services.ingestion import create_course, ingest_document, plan_chunks
from app.services.verification import verify_document


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
    print(f"  chunks: {result.chunk_count}{replaced}", flush=True)

    if args.no_embed:
        print("  status: processing - embeddings skipped")
        return 0

    # A separate session from the chunk transaction above, which has committed.
    # Embedding commits per batch and must not be able to roll that back.
    async with async_session() as session:
        return await _run_embedding(session, result.document_id)


async def _run_embedding(session, document_id: uuid.UUID) -> int:
    """Show the cost, then spend it. Shared by `ingest` and `embed`."""
    pending = await pending_chunks(session, document_id)
    if not pending:
        print("  embeddings: already complete, nothing to spend")
    else:
        cost = estimate(pending)
        # Two decimal places would print a real cost of $0.000033 as $0.00, which
        # is exactly the number this line exists to show. Small runs get the
        # precision they need; a semester-sized one reads normally.
        dollars = f"${cost.estimated_usd:.2f}" if cost.estimated_usd >= 0.01 else f"${cost.estimated_usd:.6f}"
        print(
            f"  embedding {cost.chunk_count} chunk(s), "
            f"~{cost.estimated_tokens} tokens, ~{dollars} (estimate)",
            flush=True,
        )

    result = await embed_document(session, user_id=SEED_USER_ID, document_id=document_id)
    print(f"  embedded: {result.embedded}, remaining: {result.remaining}")
    print(f"  status: {result.status}")
    return 0 if result.remaining == 0 else 1


async def _embed(args: argparse.Namespace) -> int:
    async with async_session() as session:
        print(f"document {args.document_id}")
        return await _run_embedding(session, args.document_id)


async def _verify(args: argparse.Namespace) -> int:
    async with async_session() as session:
        report = await verify_document(
            session, user_id=SEED_USER_ID, document_id=args.document_id
        )

    print(f"document {report.document_id}  {report.storage_path}")
    print(f"  chunks checked: {report.chunk_count}")
    # Flushed so the failure detail on stderr lands after this header rather than
    # ahead of it when both are going to the same terminal.
    print(f"  pages: {report.pages_extracted} extracted, {report.pages_stored} recorded", flush=True)

    if report.pages_stored != report.pages_extracted:
        print(
            "  FAILED: page count changed since ingestion - the PDF or the "
            "extraction library is not what it was",
            file=sys.stderr,
        )

    if report.failures:
        print(f"  FAILED: {len(report.failures)} chunk(s) do not match:", file=sys.stderr)
        for failure in report.failures[:10]:
            print(f"    chunk {failure.chunk_index} (page {failure.page_number}): {failure.reason}", file=sys.stderr)

    if not report.ok:
        return 1

    print(f"  OK: all {report.chunk_count} chunks slice back byte for byte.")
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
    ingest.add_argument(
        "--no-embed",
        action="store_true",
        help="store chunks but skip the embedding step, which is what costs money",
    )
    ingest.set_defaults(handler=_ingest)

    embed = subcommands.add_parser(
        "embed", help="embed a document's chunks; resumes a run that failed partway"
    )
    embed.add_argument("document_id", type=uuid.UUID)
    embed.set_defaults(handler=_embed)

    verify = subcommands.add_parser(
        "verify", help="re-extract a document's PDF and check every chunk's offsets"
    )
    verify.add_argument("document_id", type=uuid.UUID)
    verify.set_defaults(handler=_verify)

    args = parser.parse_args()
    try:
        return asyncio.run(args.handler(args))
    except ServiceError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
