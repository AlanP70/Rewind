"""The one normalisation extraction does, and the property that makes it safe.

`normalise_page_text` is pure, so this needs no PDF and no database. What it
must never do is change a length: page text is the coordinate system for every
`chunks.char_start`/`char_end`, and those offsets are recorded once and never
recomputed.

What is *not* guarded here is that `extract_pages` calls it, because the only
input that would prove it is a NUL-bearing PDF and the one we have is 260 KB of
gitignored eval corpus. That half was checked by running `verify` on lecture 16
after it ingested: verify re-extracts in a fresh process and compares byte for
byte, so it passes only if both extraction paths produce the same substituted
text. It did. A future NUL-bearing PDF small enough to commit belongs here.
"""

from hypothesis import given
from hypothesis import strategies as st

from app.services.extraction import normalise_page_text

# Page 6 of MIT6_006S20_lec16.pdf, verbatim, wrapped where the extractor wrapped
# it. This is the input that actually failed: the bullet and the arithmetic
# survive pdfminer intact and the operator between the braces does not.
LEC16_PAGE_6 = (
    "ing Coin Game\n"
    "Given sequence of ncoins of value v ,v ,...,v\n"
    "0 1 n 1\n"
    "• \x00\n"
    "Two players take turns\n"
)


def test_nul_is_replaced_and_nothing_else_moves() -> None:
    """The real failing input, and the distinction the whole choice rests on."""
    normalised = normalise_page_text(LEC16_PAGE_6)

    assert "\x00" not in normalised
    # Deleting the NUL would also satisfy the line above, and would pass a test
    # that only checked for its absence. It would also pull every character
    # after it one place earlier, on this page only, and every offset already
    # stored for this document would then point one character to the left.
    assert len(normalised) == len(LEC16_PAGE_6)
    assert normalised.index("Two players") == LEC16_PAGE_6.index("Two players")
    assert "• �\n" in normalised


def test_text_with_no_nul_is_returned_unchanged() -> None:
    """Nothing else is normalised, including things that look like mistakes.

    The blank line, the double space and the trailing whitespace are all left
    exactly as pdfplumber produced them. This is the test that fails if a
    whitespace cleanup is ever added to this function, which is the likely way
    the no-normalisation rule gets broken -- by something that reads as tidying.
    """
    page = "Lecture 9\n\n  Dijkstra’s  algorithm \n\n�\n"

    assert normalise_page_text(page) == page


@given(st.text())
def test_length_is_preserved_for_any_text(text: str) -> None:
    assert len(normalise_page_text(text)) == len(text)
    assert "\x00" not in normalise_page_text(text)
