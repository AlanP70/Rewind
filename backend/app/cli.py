"""The ingestion CLI: `python -m app.cli`.

Phase 1 runs ingestion synchronously here. There is no queue and no UI; the
worker and the upload endpoint are Phase 2, and when they arrive they call the
same services this does.
"""

import argparse
import asyncio
import logging
import statistics
import sys
import uuid
from datetime import date
from pathlib import Path

from app.core.db import async_session
from app.core.storage import get_storage, storage_key
from app.models.user import SEED_USER_ID
from app.repositories.search import count_embedded_chunks
from app.schemas.ingestion import PlannedChunk
from app.services.dating import (
    date_course_from_filenames,
    date_course_from_syllabus,
    parse_course_syllabus,
)
from app.services.errors import ServiceError
from app.services.extraction import extract_pages
from app.services.ingestion import create_course, plan_chunks
from app.services.processing import embed_with_estimate, process_document
from app.services.search import search
from app.services.syllabus_schedule import ScheduleEntry
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


def _read_schedule(path: Path) -> list[ScheduleEntry]:
    """Read a schedule from a TSV: `kind<TAB>ordinal<TAB>YYYY-MM-DD`.

    Kept now that `--syllabus` parses PDFs, because it is how a syllabus the
    parser reports as unrecognised still gets used: someone transcribes the dozen
    rows by hand and the course is dated. It is also how a parsed schedule gets a
    ground truth to be measured against, the same way slice 2's filename corpus
    works.

    Every error names its line. A schedule silently missing a row dates fewer
    documents and looks like the heuristic being cautious.
    """
    entries: list[ScheduleEntry] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError(f"{path}:{number}: expected 3 tab-separated fields")
        kind, ordinal, occurred_on = (field.strip() for field in fields)
        try:
            entries.append(
                ScheduleEntry(
                    kind=kind,
                    ordinal=int(ordinal),
                    occurred_on=date.fromisoformat(occurred_on),
                )
            )
        except ValueError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
    return entries


async def _date_course(args: argparse.Namespace) -> int:
    """Date a course's documents, and print what it could not.

    With `--syllabus` or `--schedule` the dates come from the syllabus and the
    filename supplies only the session number; without either, from filenames
    alone.

    An unreadable syllabus stops the run instead of falling through to filenames.
    Someone who passed `--syllabus` asked for those dates, and quietly giving them
    the weaker source under the same command is how a course ends up full of
    `inferred_filename` suggestions that were supposed to be facts.

    No `session.begin()` here, unlike every other command: `redate_document` owns
    its own transaction and commits per document, so wrapping the batch would
    nest one.

    Three groups, printed in descending order of how much they are believed:
    dated, offered, undated. Only the first wrote anything. A run that dates two
    of twenty and offers eleven is a good run this phase is designed to produce
    -- the rest is the list a person then works through.
    """
    schedule: list[ScheduleEntry] | None = None

    if args.syllabus:
        path = Path(args.syllabus)
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return 1
        async with async_session() as session:
            try:
                parsed = await parse_course_syllabus(
                    session,
                    user_id=SEED_USER_ID,
                    course_id=args.course_id,
                    data=path.read_bytes(),
                    name=path.name,
                )
            except ServiceError as error:
                print(error, file=sys.stderr)
                return 1
        if not parsed.entries:
            print(f"{path.name}: {parsed.reason}", file=sys.stderr)
            return 1
        skipped = f", {parsed.skipped} unnumbered row(s) skipped" if parsed.skipped else ""
        print(f"{path.name}: {len(parsed.entries)} {parsed.unit}s{skipped}")
        schedule = list(parsed.entries)
    elif args.schedule:
        try:
            schedule = _read_schedule(Path(args.schedule))
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1

    async with async_session() as session:
        try:
            if schedule is None:
                outcomes = await date_course_from_filenames(
                    session,
                    user_id=SEED_USER_ID,
                    course_id=args.course_id,
                    overwrite=args.overwrite,
                )
            else:
                outcomes = await date_course_from_syllabus(
                    session,
                    user_id=SEED_USER_ID,
                    course_id=args.course_id,
                    schedule=schedule,
                    overwrite=args.overwrite,
                )
        except ServiceError as error:
            print(error, file=sys.stderr)
            return 1

    dated = [outcome for outcome in outcomes if outcome.occurred_on]
    for outcome in dated:
        print(f"{outcome.occurred_on}  {outcome.source}  {outcome.filename}")

    # Never printed in the same column as a stored date. These documents are
    # still undated; the dates beside them are offers. Two of them is a
    # disagreement between sources that a person has to settle.
    offered = [outcome for outcome in outcomes if outcome.candidates]
    for outcome in offered:
        label = "conflict" if len(outcome.candidates) > 1 else "suggested"
        dates = " / ".join(
            f"{candidate.occurred_on} ({candidate.source})"
            for candidate in outcome.candidates
        )
        print(f"{label:<10}  {outcome.filename}  {dates}  -- {outcome.reason}")

    undated = [
        outcome
        for outcome in outcomes
        if not outcome.occurred_on and not outcome.candidates
    ]
    for outcome in undated:
        print(f"{'undated':<10}  {outcome.filename}  -- {outcome.reason}")

    print(
        f"\n{len(dated)} dated, {len(offered)} offered, {len(undated)} undated, "
        f"{len(outcomes)} documents"
    )
    return 0


async def _ingest(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 1

    # The CLI is the one entrypoint that starts from a local file. Any path on
    # this machine is fine now -- the repo-relative rule went away with
    # `storage_path`, because what is stored is a key and the bytes are copied
    # into storage rather than pointed at.
    data = path.read_bytes()

    if args.dry_run:
        pages = extract_pages(data, name=path.name)
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

    # Upload before processing, not inside it. `process_document` takes a key
    # because slice 3's worker can only ever be given one, and putting the upload
    # here keeps the CLI on the same code path the worker will use.
    #
    # Consequence accepted: an upload whose request then fails a precondition --
    # an unknown course, say -- leaves the object behind. Phase 2 has no delete
    # path and does not grow one for this.
    key = storage_key(SEED_USER_ID, path.name)
    await get_storage().upload(key, data)
    logging.getLogger("app").info("uploaded %s (%d bytes)", key, len(data))

    # `process_document` owns its own commits -- it has to, because the
    # `processing_runs` row must be durable while the work is still running.
    async with async_session() as session:
        result = await process_document(
            session,
            user_id=SEED_USER_ID,
            course_id=args.course_id,
            storage_key=key,
            kind=args.kind,
            title=args.title or path.stem,
            force=args.force,
            embed=not args.no_embed,
        )

    # Progress is logged by the service, not printed here, so the worker reports
    # the same things in slice 3. What is left is the exit code.
    return 0 if result.embedding is None or result.embedding.remaining == 0 else 1


async def _embed(args: argparse.Namespace) -> int:
    async with async_session() as session:
        result = await embed_with_estimate(
            session, user_id=SEED_USER_ID, document_id=args.document_id
        )
    return 0 if result.remaining == 0 else 1


async def _search(args: argparse.Namespace) -> int:
    """Run one query and show what came back, with both timings.

    `--repeat` exists for the latency measurement rather than for searching: the
    first query of a process pays for the connection pool and the first OpenAI
    TLS handshake, so a single reading is mostly setup. The embedding is repeated
    with the query, deliberately -- it is what a user actually waits for, and
    separating the two numbers is what will show whether slice 3's index moved
    the half it can move.
    """
    embed_times: list[float] = []
    query_times: list[float] = []
    results = None

    async with async_session() as session:
        for _ in range(args.repeat):
            results = await search(
                session,
                user_id=SEED_USER_ID,
                query=args.query,
                limit=args.limit,
                course_id=args.course_id,
            )
            embed_times.append(results.embed_ms)
            query_times.append(results.query_ms)

    assert results is not None  # --repeat is >= 1, enforced by argparse

    for rank, hit in enumerate(results.hits, 1):
        date_note = (
            f"{hit.occurred_at:%Y-%m-%d} ({hit.occurred_at_source})"
            if hit.occurred_at
            else "undated"
        )
        print(
            f"{rank:>3}. {hit.distance:.4f}  {hit.document_title} "
            f"p{hit.page_number}  {date_note}"
        )
        print(f"      {_snippet(hit.content)}")

    print(f"\n{len(results.hits)} hit(s) over {await _corpus_size(args.course_id)}")
    print(f"  embed: {_timing(embed_times)}")
    print(f"  query: {_timing(query_times)}")
    return 0


def _timing(samples: list[float]) -> str:
    """Median and range, not a mean.

    One slow sample -- a TLS handshake, an autovacuum -- drags a mean of five
    readings far enough to change the conclusion, and the honest summary of a
    handful of measurements is the middle one plus what the spread was.
    """
    if len(samples) == 1:
        return f"{samples[0]:.1f} ms"
    return (
        f"{statistics.median(samples):.1f} ms median "
        f"({min(samples):.1f}-{max(samples):.1f} ms over {len(samples)} runs)"
    )


async def _corpus_size(course_id: uuid.UUID | None) -> str:
    """What the timings are a measurement *of*. A latency with no corpus size
    next to it is not a result -- it cannot be compared to anything later."""
    async with async_session() as session:
        counts = await count_embedded_chunks(
            session, user_id=SEED_USER_ID, course_id=course_id
        )
    scope = "this course" if course_id else "all courses"
    return (
        f"{counts.embedded_chunks} embedded chunk(s) in "
        f"{counts.documents} document(s), {scope}"
    )


def _snippet(content: str, width: int = 96) -> str:
    """One line of the passage. Newlines collapsed for the terminal only -- the
    stored content is untouched, and its offsets still index the page."""
    flat = " ".join(content.split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


async def _verify(args: argparse.Namespace) -> int:
    async with async_session() as session:
        report = await verify_document(
            session, user_id=SEED_USER_ID, document_id=args.document_id
        )

    print(f"document {report.document_id}  {report.storage_key}")
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
    # Services report progress through `logging`, so the identical lines land in
    # the worker's log once slice 3 exists. Bare format because the audience here
    # is a person at a terminal, not a log aggregator.
    #
    # INFO is raised for `app` alone, not the root logger: httpx logs every
    # request at INFO, so a global INFO turns each embedding batch into a line of
    # URL noise between the lines worth reading.
    # `search` prints real course material, and course material contains
    # characters a Windows console code page cannot encode -- a Greek delta in a
    # complexity bound is enough. Without this the command dies with
    # UnicodeEncodeError partway down its own results, having already printed
    # some, which reads as a bug in search rather than in printing.
    #
    # `errors` only, not `encoding`: the terminal's encoding is the terminal's
    # business, and forcing UTF-8 onto a cp1252 console trades a crash for
    # mojibake. A `?` where a glyph cannot be shown is honest, and the stored text
    # -- which is what every offset indexes into -- is untouched either way.
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stdout)
    logging.getLogger("app").setLevel(logging.INFO)

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

    dating = subcommands.add_parser(
        "date-course", help="date a course's documents from their filenames"
    )
    dating.add_argument("course_id", type=uuid.UUID)
    # One source or the other. Both would need a rule for which wins, and the two
    # exist precisely because the parser cannot read every syllabus -- the TSV is
    # the manual answer to a PDF it refused.
    from_syllabus = dating.add_mutually_exclusive_group()
    from_syllabus.add_argument(
        "--syllabus",
        help="the course's syllabus as a PDF. Reads its schedule, or reports the "
        "format as unrecognized and stops.",
    )
    from_syllabus.add_argument(
        "--schedule",
        help="TSV of `kind<TAB>ordinal<TAB>YYYY-MM-DD` from the course's syllabus, "
        "for a syllabus `--syllabus` cannot read. Without either, dates come from "
        "filenames alone.",
    )
    dating.add_argument(
        "--overwrite",
        action="store_true",
        help="re-infer dates that inference already set, e.g. after fixing the "
        "course's term bounds. Never touches a date set by hand.",
    )
    dating.set_defaults(handler=_date_course)

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

    searching = subcommands.add_parser(
        "search", help="rank chunks against a question, nearest first"
    )
    searching.add_argument("query")
    # No default course. Cross-course is the product, so scoping is the thing you
    # have to ask for.
    searching.add_argument(
        "--course-id", type=uuid.UUID, help="search one course instead of all of them"
    )
    searching.add_argument("--limit", type=int, default=10)
    searching.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run the same query N times and report median latency and range",
    )
    searching.set_defaults(handler=_search)

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
