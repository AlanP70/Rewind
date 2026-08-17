"""Request and response bodies for `POST /search`."""

import uuid
from datetime import datetime

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
