"""Turning ranked chunks into a chronological timeline.

One pure function over search hits. It exists as a service rather than inside
the eval because the eval and the UI must answer "which document is first" the
same way -- an eval that measures a rule the interface does not ship is worse
than no eval, since it reports a number for something nobody uses. The UI is
next slice; this is written once and called twice.

Three rules here are decisions, not implementation details, and each one is a
consequence of how weakly this corpus is dated:

- **Hits group per document, not per chunk.** Three passages from lecture 9 are
  one place the concept appeared, not three.
- **A tie for earliest badges every document in the tie**, labelled earliest and
  counted. Picking one would invent a precision the dates do not have.
- **The badge is suppressed entirely whenever any undated document also
  matches.** An undated match's position in the ordering is unknown, so
  "earliest match" is undetermined rather than merely unproven. Undated matches
  are still returned, grouped and visible -- never dropped, because dropping them
  would make the suppressed badge look like a bug rather than an answer.

**What the badge claims, settled in slice 4: "earliest match" -- the oldest of
the documents this query retrieved. Not "first occurrence in the corpus".** The
distinction is the whole reason there is no relevance threshold in this module.
Every document that matched counts, because the alternative is a cutoff that
decides where mention ends and teaching begins, and nothing in the ground truth
encodes that line. Four such rules were measured and all four were rejected;
they are recorded in ROADMAP's "Settled: stop claiming first" so they are not
re-proposed. **A caller that adds a threshold here is changing what the product
promises, not tuning it** -- and the stronger claim needs a different query shape
entirely (filter-then-sort-by-date over the corpus, not top-k), which is why it
is Deferred rather than a constant waiting to be picked.
"""

from dataclasses import dataclass
from datetime import datetime

from app.models import OccurredAtSource
from app.schemas.search import SearchHit


@dataclass(frozen=True)
class TimelineDocument:
    """Every hit that landed in one document, plus where that document sits."""

    document_id: object
    course_id: object
    document_title: str
    course_name: str
    occurred_at: datetime | None
    occurred_at_source: OccurredAtSource | None

    # Best first. `best_distance` is the first hit's, kept separately because it
    # is what orders documents against each other.
    hits: tuple[SearchHit, ...]
    best_distance: float

    # Never true when `Timeline.badge_suppressed` is set.
    is_earliest: bool


@dataclass(frozen=True)
class Timeline:
    """Dated documents in order, undated ones alongside, and the badge decision."""

    dated: tuple[TimelineDocument, ...]
    undated: tuple[TimelineDocument, ...]

    # How many documents share the earliest date. 2 is a real answer and renders
    # as "earliest (2)"; 0 means nothing was badged.
    earliest_count: int

    # True when an undated document matched. Distinguishes "no badge because an
    # undated match could precede it" from "no badge because nothing matched",
    # which look identical from `earliest_count` alone.
    badge_suppressed: bool


def build_timeline(hits: list[SearchHit]) -> Timeline:
    """Group `hits` by document and decide which document is first.

    Ordering is by `occurred_at` only -- the timeline is an ordering, not a
    scale. No proportional axis, no gaps drawn to size: interpolated spacing is
    exactly the precision this corpus does not have.
    """
    grouped: dict[object, list[SearchHit]] = {}
    for hit in hits:
        grouped.setdefault(hit.document_id, []).append(hit)

    documents = [_document(document_hits) for document_hits in grouped.values()]

    dated = [document for document in documents if document.occurred_at is not None]
    undated = [document for document in documents if document.occurred_at is None]

    badge_suppressed = bool(undated)
    earliest_count = 0

    if dated and not badge_suppressed:
        earliest = min(document.occurred_at for document in dated)
        dated = [
            _badged(document, is_earliest=document.occurred_at == earliest)
            for document in dated
        ]
        earliest_count = sum(1 for document in dated if document.is_earliest)

    return Timeline(
        # `best_distance` breaks a date tie so the order is total and stable;
        # two documents dated the same day would otherwise come back in
        # whatever order the dict happened to hold them.
        dated=tuple(sorted(dated, key=lambda d: (d.occurred_at, d.best_distance))),
        undated=tuple(sorted(undated, key=lambda d: d.best_distance)),
        earliest_count=earliest_count,
        badge_suppressed=badge_suppressed,
    )


def _document(hits: list[SearchHit]) -> TimelineDocument:
    ranked = tuple(sorted(hits, key=lambda hit: hit.distance))
    first = ranked[0]
    return TimelineDocument(
        document_id=first.document_id,
        course_id=first.course_id,
        document_title=first.document_title,
        course_name=first.course_name,
        occurred_at=first.occurred_at,
        occurred_at_source=first.occurred_at_source,
        hits=ranked,
        best_distance=first.distance,
        is_earliest=False,
    )


def _badged(document: TimelineDocument, *, is_earliest: bool) -> TimelineDocument:
    return TimelineDocument(
        document_id=document.document_id,
        course_id=document.course_id,
        document_title=document.document_title,
        course_name=document.course_name,
        occurred_at=document.occurred_at,
        occurred_at_source=document.occurred_at_source,
        hits=document.hits,
        best_distance=document.best_distance,
        is_earliest=is_earliest,
    )
