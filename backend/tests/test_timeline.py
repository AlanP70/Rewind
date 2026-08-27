"""The first-occurrence rule, on hand-built hits where the answer is known.

No database and no embeddings: `build_timeline` is a pure function, and the
three decisions in it are decisions about *this corpus's dating*, not about
retrieval. Each test below is built so that the obvious wrong implementation
answers differently -- the nearest document is never the earliest one, because a
timeline that quietly ordered by distance would pass any fixture where those
coincide.
"""

import inspect
import uuid
from datetime import UTC, datetime

from app.models import OccurredAtSource
from app.schemas.search import SearchHit, SearchRequest, TimelineResults
from app.services.timeline import badge_earliest, build_timeline, timeline_results


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


def test_documents_considered_counts_undated_matches_too() -> None:
    """The badge's denominator, and it is counted before the dated/undated split.

    Two dated and one undated is three documents considered, not two. The
    plausible wrong implementation is `len(timeline.dated)` -- which is right on
    every fixture where nothing is undated, and which would quietly turn
    "earliest of 3" into "earliest of 2" in exactly the case where the badge is
    suppressed and the count is doing all the explaining.
    """
    timeline = build_timeline(
        [
            hit("lec3", distance=0.20, occurred_at=on(3)),
            hit("lec9", distance=0.30, occurred_at=on(9)),
            hit("mystery", distance=0.50, occurred_at=None),
        ]
    )

    assert timeline.documents_considered == 3
    assert len(timeline.dated) == 2


def test_documents_considered_counts_documents_not_chunks() -> None:
    """Three passages from one lecture are one document considered."""
    timeline = build_timeline(
        [
            hit("lec9", distance=0.10, occurred_at=on(9)),
            hit("lec9", distance=0.20, occurred_at=on(9)),
            hit("lec9", distance=0.30, occurred_at=on(9)),
        ]
    )

    assert timeline.documents_considered == 1


def results(timeline) -> TimelineResults:
    return timeline_results(timeline, query="q", embed_ms=0.0, query_ms=0.0)


def test_badge_earliest_is_undetermined_when_any_date_is_missing() -> None:
    """The rule in isolation, on the type it actually reasons about."""
    assert badge_earliest([on(9), on(3)]) == on(3)
    assert badge_earliest([on(9), None, on(3)]) is None
    assert badge_earliest([]) is None


def test_badge_earliest_cannot_see_relevance() -> None:
    """A tripwire, not a behaviour test.

    The badge decision takes dates and nothing else. Widening this signature to
    accept distances, ranks or hit counts is how a relevance threshold arrives
    without anyone deciding to add one -- slice 4 settled that the badge claims
    "earliest match" over everything that matched, and that promise is only kept
    if the function making the claim has no relevance to consult.

    The cheapest way to make this test pass again is to edit this list, which is
    the point: it puts the change in the diff next to the sentence explaining what
    it costs.
    """
    assert list(inspect.signature(badge_earliest).parameters) == ["dates"]


def test_no_relevance_cutoff_can_enter_the_search_contract() -> None:
    """The same tripwire on the wire, where a knob would be exposed to callers.

    `limit` is a cap on how much is retrieved, not a cutoff on what counts as a
    match -- everything retrieved reaches the timeline and is counted. A field
    like `min_similarity` would change the badge from "earliest of what matched"
    to "earliest above a number nobody can justify", which is a different product
    promise wearing the clothes of a parameter.
    """
    assert set(SearchRequest.model_fields) == {"query", "course_id", "limit"}


def test_documents_considered_is_required_on_the_wire() -> None:
    """No default. A `= 0` would make the field optional to construct, and the
    first response built without it would claim the badge was made over nothing
    while still rendering a badge."""
    assert TimelineResults.model_fields["documents_considered"].is_required()


def test_the_badge_variant_name_crosses_the_wire() -> None:
    """Three claims, three names. The interface switches on `claim` rather than on
    `earliest_count == 0`, which collapses two of them."""
    badged = results(
        build_timeline(
            [
                hit("lec3", distance=0.40, occurred_at=on(3)),
                hit("lec9", distance=0.10, occurred_at=on(9)),
            ]
        )
    )
    assert badged.badge.claim == "earliest-match"
    assert len(badged.badge.document_ids) == 1
    assert badged.dated[0].document_id == badged.badge.document_ids[0]
    assert badged.documents_considered == 2

    suppressed = results(
        build_timeline(
            [
                hit("lec3", distance=0.20, occurred_at=on(3)),
                hit("mystery", distance=0.50, occurred_at=None),
            ]
        )
    )
    assert suppressed.badge.claim == "undetermined"
    assert suppressed.badge.undated_count == 1
    # Suppressed, but still two documents considered and both still returned --
    # otherwise the missing badge has no visible cause.
    assert suppressed.documents_considered == 2
    assert len(suppressed.undated) == 1

    assert results(build_timeline([])).badge.claim == "no-matches"


def test_a_tie_puts_every_earliest_document_in_the_badge() -> None:
    """The count the badge renders is the length of `document_ids`, so a tie
    cannot render as one document with a "(2)" beside it."""
    timeline = results(
        build_timeline(
            [
                hit("lec4", distance=0.10, occurred_at=on(3)),
                hit("lec3", distance=0.30, occurred_at=on(3)),
                hit("lec9", distance=0.20, occurred_at=on(9)),
            ]
        )
    )

    assert timeline.badge.claim == "earliest-match"
    assert len(timeline.badge.document_ids) == 2
    assert set(timeline.badge.document_ids) == {
        d.document_id for d in timeline.dated if d.document_title in {"lec3", "lec4"}
    }


def test_a_hit_on_the_wire_carries_no_date_of_its_own() -> None:
    """Dates live on the document group, not on individual passages.

    A `occurred_at` on a hit would let a renderer place one passage in the
    timeline independently of the document it came from, which is how the "hits
    group per document" decision gets undone one component at a time.
    """
    timeline = results(
        build_timeline([hit("lec3", distance=0.20, occurred_at=on(3))])
    )
    hit_fields = set(type(timeline.dated[0].hits[0]).model_fields)

    assert "occurred_at" not in hit_fields
    assert "occurred_at_source" not in hit_fields
    assert {"page_number", "char_start", "char_end"} <= hit_fields
