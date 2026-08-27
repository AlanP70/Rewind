"""Request and response bodies for `POST /search`."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models import OccurredAtSource


class SearchRequest(BaseModel):
    """A question, and optionally where to look.

    POST rather than GET with a query string, because the body is a question a
    person typed: it can be long, it contains punctuation, and it is not a
    cacheable identifier for a resource.
    """

    query: str = Field(min_length=1, max_length=1000)
    course_id: uuid.UUID | None = None

    # Chunks, not documents. The timeline groups hits per document, so 20 chunks
    # may be five documents or twenty; the grouping happens above this.
    limit: int = Field(default=20, ge=1, le=100)


class SearchHit(BaseModel):
    """One chunk that matched, with everything the timeline needs to place it.

    `occurred_at` and `occurred_at_source` are the document's, carried here so the
    timeline can order and label a hit without a second request -- and so it can
    see that a hit is *undated*, which is what suppresses the first-occurrence
    badge rather than being quietly dropped.

    `page_number`, `char_start` and `char_end` travel together because they are
    the deep link: the page opens the PDF, the offsets place our own highlight.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    course_id: uuid.UUID
    document_title: str
    course_name: str
    page_number: int
    char_start: int
    char_end: int
    content: str

    # Cosine distance, 0 = identical. Reported raw rather than as a percentage:
    # there is no calibration behind it, and a "94% match" invents one.
    distance: float

    occurred_at: datetime | None
    occurred_at_source: OccurredAtSource | None


class SearchResults(BaseModel):
    """The ranked hits, and how long each half of the work took.

    The two timings are separate on purpose. Embedding the query is one HTTPS
    round trip to OpenAI and dominates the total; the database time is what an
    index can change. One combined number would let an index that halved the
    query look like it did nothing, and would let a slow network look like a slow
    query.
    """

    query: str
    hits: list[SearchHit]
    embed_ms: float
    query_ms: float


class TimelineHit(BaseModel):
    """One passage inside a document group. Chunk-level fields only.

    Deliberately narrower than `SearchHit`: the document's title, course and date
    live on the group above, so a renderer cannot read a date off an individual
    passage and place it independently. `page_number`, `char_start` and
    `char_end` still travel together -- they are the deep link, and the offsets
    place our own highlight over the native PDF viewer.
    """

    chunk_id: uuid.UUID
    page_number: int
    char_start: int
    char_end: int
    content: str

    # Cosine distance, 0 = identical. Raw, not a percentage: there is no
    # calibration behind it and a "94% match" invents one.
    distance: float


class TimelineEntry(BaseModel):
    """One document that matched, with every passage that landed in it."""

    document_id: uuid.UUID
    course_id: uuid.UUID
    document_title: str
    course_name: str
    occurred_at: datetime | None
    occurred_at_source: OccurredAtSource | None

    # Best first, so the deep link goes to the strongest passage.
    hits: list[TimelineHit]


class EarliestMatch(BaseModel):
    """The badge is shown: these documents are the oldest ones this query found.

    Not "the first time you learned this". `claim` says so on the wire, and it is
    the field a renderer switches on -- so a variant added later cannot be
    rendered with the old sentence by accident.
    """

    claim: Literal["earliest-match"] = "earliest-match"

    # Every document in a tie for the earliest date. The count the badge renders
    # is this list's length; a separate `count` field could disagree with it.
    document_ids: list[uuid.UUID]


class Undetermined(BaseModel):
    """No badge: an undated document matched, so which is earliest is unknown.

    Distinct from finding nothing, and the difference has to reach the interface
    -- otherwise a suppressed badge looks like a bug rather than an answer.
    """

    claim: Literal["undetermined"] = "undetermined"
    undated_count: int


class NoMatches(BaseModel):
    """No badge because nothing matched at all."""

    claim: Literal["no-matches"] = "no-matches"


Badge = Annotated[EarliestMatch | Undetermined | NoMatches, Field(discriminator="claim")]


class TimelineResults(BaseModel):
    """What `POST /search` returns: an ordering of documents, and one claim about it.

    **There is no threshold field here, and adding one would be visible.** Every
    document that matched is in `dated` or `undated` and is counted in
    `documents_considered`; nothing is filtered by relevance on the way. A cutoff
    would decide where mention ends and teaching begins, which is a change to what
    the badge promises rather than a tuning knob -- see `services/timeline.py`.

    `documents_considered` is required, has no default, and is computed before the
    dated/undated split, so it is the size of the set the claim was made over. A
    badge over two documents and a badge over nineteen are different claims, and
    this is the only field that says which one the user is looking at.
    """

    query: str
    badge: Badge
    documents_considered: int
    dated: list[TimelineEntry]
    undated: list[TimelineEntry]

    # Separate on purpose: embedding is one HTTPS round trip to OpenAI and
    # dominates the total, while the database time is what an index can change.
    embed_ms: float
    query_ms: float
