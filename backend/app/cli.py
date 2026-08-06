"""The ingestion CLI: `python -m app.cli`.

Phase 1 runs ingestion synchronously here. There is no queue and no UI; the
worker and the upload endpoint are Phase 2, and when they arrive they call the
same services this does.
"""

import argparse
import statistics
import sys
from pathlib import Path

from app.services.extraction import extract_pages
from app.services.ingestion import PlannedChunk, plan_chunks


def _check_offsets(pages: list[str], planned: list[PlannedChunk]) -> list[str]:
    """Layer (b): assert every chunk's offsets slice its page back to its content.

    In-process, so it cannot catch non-deterministic extraction or corruption on
    the way through Postgres -- that is what `verify` is for, in slice 3. What it
    does catch is the chunker and `plan_chunks` disagreeing about a real PDF's
    text, which synthetic strings in the property test may not provoke.
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


def _ingest(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 1

    if not args.dry_run:
        # Persistence is the next slice. Saying so beats a confusing traceback.
        print("only --dry-run is implemented so far; persistence lands next.", file=sys.stderr)
        return 1

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


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subcommands = parser.add_subparsers(dest="command", required=True)

    ingest = subcommands.add_parser("ingest", help="ingest a PDF into a course")
    ingest.add_argument("course_id")
    ingest.add_argument("path")
    ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="extract, chunk and check offsets without writing anything",
    )
    ingest.set_defaults(handler=_ingest)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
