"""Apply `lecture_dates.tsv` to the eval corpus, through the sole writer.

The eval cannot run against an undated corpus: `build_timeline` suppresses the
badge whenever an undated document matches, so every question would score
`unrankable` and the phase's headline claim would go unmeasured. This script is
what puts dates on the 20 lectures.

**The dates it applies are constructed, not MIT's** -- the full reasoning, and
the argument for why the tally is invariant to them, is in the header of
`lecture_dates.tsv`. Read that file before citing any number this corpus
produced.

Not stdlib-only, unlike `fetch_corpus.py`, and the reason is invariant 4: this
writes `documents.occurred_at`, and **exactly one function is allowed to do
that**. Reaching for `UPDATE documents SET occurred_at` here would be a second
writer -- the precise thing the funnel and its AST test exist to prevent -- so
this goes through `redate_document` like every other dating path, and inherits
its term-bounds check and its Phase 5 cascade for free.
"""

import argparse
import asyncio
import pathlib
import sys
import uuid
from datetime import date

from sqlalchemy import select

from app.core.db import async_session
from app.models import Document, OccurredAtSource
from app.models.user import SEED_USER_ID
from app.services.dating import redate_document
from app.services.retrieval_eval import lecture_ordinal

DATES = pathlib.Path(__file__).parent / "lecture_dates.tsv"


def scheduled() -> dict[int, date]:
    """`lecture_dates.tsv` as ordinal -> day. Comments and blanks skipped."""
    out = {}
    for line in DATES.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ordinal, on = line.split("\t")
        out[int(ordinal)] = date.fromisoformat(on)
    return out


async def apply(course_id: uuid.UUID) -> int:
    dates = scheduled()

    async with async_session() as session:
        documents = (
            await session.execute(
                select(Document).where(Document.course_id == course_id)
            )
        ).scalars().all()
        # Read before any write: a partial dating is worse than none, because the
        # badge rule would then rank a dated half against an undated half and
        # report a number for it.
        plan = []
        for document in documents:
            ordinal = lecture_ordinal(document.title)
            if ordinal is None or ordinal not in dates:
                print(f"no date for {document.title!r} (ordinal {ordinal})", file=sys.stderr)
                return 1
            plan.append((ordinal, document.id, document.title))

    for ordinal, document_id, title in sorted(plan):
        async with async_session() as session:
            result = await redate_document(
                session,
                user_id=SEED_USER_ID,
                document_id=document_id,
                occurred_on=dates[ordinal],
                source=OccurredAtSource.MANUAL,
            )
        flag = "  OUTSIDE TERM" if result.outside_term else ""
        print(f"lec{ordinal:<3} {dates[ordinal]}  {title}{flag}")

    print(f"\n{len(plan)} documents dated `manual` from {DATES.name}.")
    print("These dates are constructed, not MIT's. See that file's header.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course_id", type=uuid.UUID)
    return asyncio.run(apply(parser.parse_args().course_id))


if __name__ == "__main__":
    raise SystemExit(main())
