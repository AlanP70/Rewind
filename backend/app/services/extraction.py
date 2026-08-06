"""PDF text extraction.

One job: turn a PDF into one string per page. Those strings are the coordinate
system every `chunks.char_start`/`char_end` indexes into, so this module has a
rule it does not break:

**The text is returned exactly as pdfplumber produced it. No stripping, no
whitespace collapsing, no normalisation of any kind.**

Any transformation applied here becomes part of the coordinate system, and
`verify` re-extracts in a fresh process and compares byte for byte. A cleanup
step that lives on only one of those two paths turns every offset into a lie.
If normalisation is ever wanted, it goes in one named function called by both.
"""

from pathlib import Path

import pdfplumber


def extract_pages(path: Path) -> list[str]:
    """Return one string per page, in document order.

    Index `i` of the result is page number `i + 1`; `page_number` is 1-based in
    the database and this is the only place that conversion happens.

    Pages with no extractable text come back as `""` rather than being skipped,
    so the list index stays aligned with the page number. An image-only slide
    deck is a legitimately empty page, not an error.
    """
    with pdfplumber.open(path) as pdf:
        # extract_text() returns None for a page with no text objects.
        return [page.extract_text() or "" for page in pdf.pages]
