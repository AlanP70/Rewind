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

import json
import subprocess
import sys
from pathlib import Path

import pdfplumber

from app.core.paths import BACKEND_DIR


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


def extract_pages_in_subprocess(path: Path) -> list[str]:
    """The same thing, in a fresh interpreter. This is what `verify` calls.

    Verification that reuses pages already in memory can only prove that a list
    equals itself. Extracting again in a process that shares nothing is what
    makes the check able to fail: it re-reads the file from disk, re-runs
    pdfplumber, and so catches extraction that is not deterministic across runs
    or across a library version bump -- exactly the failure the `==` pins exist
    to make loud, and the one that would otherwise surface much later as
    highlights landing on the wrong passage.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "app.services.extraction", str(path)],
        capture_output=True,
        # ASCII-escaped JSON crosses the pipe, so this decode is safe regardless
        # of the console code page.
        text=True,
        encoding="utf-8",
        cwd=BACKEND_DIR,
        check=True,
    )
    return json.loads(completed.stdout)


if __name__ == "__main__":
    # Written rather than printed, and ASCII-escaped by default: this stdout is a
    # pipe carrying data, and the Windows console code page (cp1252) cannot encode
    # characters this corpus actually contains, such as U+2022.
    sys.stdout.write(json.dumps(extract_pages(Path(sys.argv[1]))))
