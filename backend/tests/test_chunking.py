"""Layer (a) of the offset verification: the pure chunker, against synthetic
strings. No PDF, no database.

The point of doing it with Hypothesis rather than hand-picked cases: the
property has to hold for *all* input text, including whatever a real PDF
produces. Hand-written cases only test what we thought of.
"""

from hypothesis import given
from hypothesis import strategies as st

from app.services.chunking import OVERLAP, TARGET_SIZE, chunk_page

# Built from fragments rather than single characters so that multi-character
# separators -- paragraph breaks, sentence ends -- actually occur. Sampling one
# character at a time almost never produces ".\n" or "\n\n" next to each other,
# and those are exactly the cases the chunker branches on. Empty and
# whitespace-only strings are in range on purpose.
page_text = st.lists(
    st.sampled_from(
        ["a", "bc", " ", ".", "!", "?", "\n", "\t", '"', "'", ")", "\n\n", ". ", "the ", "graph "]
    ),
    max_size=1200,
).map("".join)


@given(page_text)
def test_chunker_returns_only_indices_never_text(text: str) -> None:
    """The design rule that makes `text[start:end] == content` true by
    construction: the chunker cannot hand back text, so there is no second code
    path for content to disagree with.

    **Do not "fix" this into a content comparison.** Layer (a) has nothing to
    compare against -- `chunk_page` returns no content, so any slice-back
    assertion written here reduces to `text[a:b] == text[a:b]`, which passes
    unconditionally and tests nothing. That comparison is only meaningful once
    content exists as a separate artifact: at layer (b), `--dry-run` over a real
    PDF, and at layer (c), `verify` against what Postgres actually stored. What
    this test locks in is the rule that keeps those two layers honest.
    """
    for span in chunk_page(text):
        assert isinstance(span, tuple)
        assert [type(value) for value in span] == [int, int]


@given(page_text)
def test_every_span_is_non_empty_and_in_bounds(text: str) -> None:
    """`chunks` has CHECK (char_end > char_start) and CHECK (char_start >= 0).
    A span that violates either would be rejected by Postgres at insert time."""
    for start, end in chunk_page(text):
        assert start >= 0
        assert end > start
        assert end <= len(text)


@given(page_text)
def test_spans_are_ordered_and_advance(text: str) -> None:
    """Chunk order is reading order, and the walk always makes progress. A
    non-advancing walk is the failure mode that produces thousands of near
    identical chunks instead of hanging outright."""
    spans = chunk_page(text)
    for (prev_start, prev_end), (next_start, next_end) in zip(spans, spans[1:]):
        assert next_start > prev_start
        assert next_end > prev_end


@given(page_text)
def test_spans_cover_the_whole_page(text: str) -> None:
    """No character of the page falls between two chunks. A gap is silent data
    loss: the text is in the PDF, is never stored, and never turns up in a
    search result."""
    spans = chunk_page(text)
    if not text.strip():
        assert spans == []
        return

    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        assert next_start <= prev_end


@given(page_text)
def test_chunks_overlap_when_there_is_more_than_one(text: str) -> None:
    """Overlap is the whole reason a concept straddling a boundary survives
    whole in at least one chunk. If a tuning change silently drops it, the
    retrieval regression in Phase 4 would be very hard to trace back here."""
    spans = chunk_page(text)
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        assert prev_end - next_start == OVERLAP


@given(page_text)
def test_no_chunk_exceeds_the_target_size(text: str) -> None:
    """The embedding model has a token limit; an unbounded chunk is what would
    breach it."""
    for start, end in chunk_page(text):
        assert end - start <= TARGET_SIZE
