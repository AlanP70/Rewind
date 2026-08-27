"""The first-occurrence rule, on hand-built hits where the answer is known.

No database and no embeddings: `build_timeline` is a pure function, and the
three decisions in it are decisions about *this corpus's dating*, not about
retrieval. Each test below is built so that the obvious wrong implementation
answers differently -- the nearest document is never the earliest one, because a
timeline that quietly ordered by distance would pass any fixture where those
coincide.
"""

import uuid
from datetime import UTC, datetime

from app.models import OccurredAtSource
from app.schemas.search import SearchHit
from app.services.timeline import build_timeline


def hit(
    title: str,
    *,
    distance: float,
    occurred_at: datetime | None,
    document_id: uuid.UUID | None = None,
    page_number: int = 2,
) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        document_id=document_id or uuid.uuid5(uuid.NAMESPACE_DNS, title),
        course_id=uuid.uuid5(uuid.NAMESPACE_DNS, "course"),
        document_title=title,
        course_name="6.006",
        page_number=page_number,
        char_start=0,
        char_end=10,
        content=f"{title} passage",
        distance=distance,
        occurred_at=occurred_at,
        occurred_at_source=OccurredAtSource.MANUAL if occurred_at else None,
    )


def on(day: int) -> datetime:
    return datetime(2020, 3, day, tzinfo=UTC)


def test_three_chunks_from_one_document_are_one_entry() -> None:
    """Grouping is per document. Three passages from one lecture are one place
    the concept appeared, not three, and the entry keeps all three."""
    timeline = build_timeline(
        [
            hit("lec9", distance=0.30, occurred_at=on(9)),
            hit("lec9", distance=0.10, occurred_at=on(9)),
            hit("lec9", distance=0.20, occurred_at=on(9)),
        ]
    )

    assert len(timeline.dated) == 1
    assert len(timeline.dated[0].hits) == 3
    # Best first within the document, so the deep link goes to the strongest
    # passage rather than to whichever chunk the database happened to return.
    assert [h.distance for h in timeline.dated[0].hits] == [0.10, 0.20, 0.30]
    assert timeline.dated[0].best_distance == 0.10


def test_earliest_is_the_oldest_document_not_the_nearest() -> None:
    """The mutation this fixture exists to catch.

    `lec3` is the earliest and the *worst* match; `lec15` is the nearest. An
    implementation that badged the top hit would pass any fixture where the best
    match is also the oldest, which is most of them.
    """
    timeline = build_timeline(
        [
            hit("lec15", distance=0.10, occurred_at=on(15)),
            hit("lec3", distance=0.40, occurred_at=on(3)),
            hit("lec9", distance=0.20, occurred_at=on(9)),
        ]
    )

    assert [d.document_title for d in timeline.dated] == ["lec3", "lec9", "lec15"]
    assert [d.is_earliest for d in timeline.dated] == [True, False, False]
    assert timeline.earliest_count == 1
    assert timeline.badge_suppressed is False


def test_a_tie_for_earliest_badges_both_and_counts_them() -> None:
    """No tiebreak is invented. Two lectures on the same day are both earliest,
    and the count is what the badge renders."""
    timeline = build_timeline(
        [
            hit("lec4", distance=0.10, occurred_at=on(3)),
            hit("lec3", distance=0.30, occurred_at=on(3)),
            hit("lec9", distance=0.20, occurred_at=on(9)),
        ]
    )

    assert timeline.earliest_count == 2
    assert {d.document_title for d in timeline.dated if d.is_earliest} == {"lec3", "lec4"}
    # Same date, so distance orders them -- otherwise the order is whatever the
    # grouping dict held and two runs could disagree.
    assert [d.document_title for d in timeline.dated] == ["lec4", "lec3", "lec9"]


def test_one_undated_match_suppresses_the_badge_for_everything() -> None:
    """"First" is a claim about the whole corpus. An undated document could
    precede every dated one, so nothing is badged at all -- not "the earliest of
    the ones we happen to know about", which is a different and false claim."""
    timeline = build_timeline(
        [
            hit("lec3", distance=0.20, occurred_at=on(3)),
            hit("mystery", distance=0.50, occurred_at=None),
        ]
    )

    assert timeline.badge_suppressed is True
    assert timeline.earliest_count == 0
    assert all(not d.is_earliest for d in timeline.dated)


def test_undated_matches_are_returned_not_dropped() -> None:
    """Dropping them would make a suppressed badge look like a bug: the user
    would see a dated result with no badge and no reason for its absence."""
    timeline = build_timeline(
        [
            hit("lec3", distance=0.20, occurred_at=on(3)),
            hit("mystery", distance=0.50, occurred_at=None),
        ]
    )

    assert [d.document_title for d in timeline.undated] == ["mystery"]
    assert [d.document_title for d in timeline.dated] == ["lec3"]


def test_nothing_matching_is_not_the_same_as_nothing_dated() -> None:
    """`earliest_count == 0` has two causes and they need different words in the
    interface, so `badge_suppressed` distinguishes them."""
    empty = build_timeline([])
    suppressed = build_timeline([hit("mystery", distance=0.5, occurred_at=None)])

    assert empty.earliest_count == 0 and empty.badge_suppressed is False
    assert suppressed.earliest_count == 0 and suppressed.badge_suppressed is True
