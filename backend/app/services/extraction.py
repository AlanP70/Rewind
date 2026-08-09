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

import io
import json
import subprocess
import sys

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException

from app.core.paths import BACKEND_DIR
from app.services.errors import ServiceError


def extract_pages(data: bytes, *, name: str) -> list[str]:
    """Return one string per page, in document order.

    Takes bytes rather than a path because the PDF now comes from storage, which
    may be an HTTP object the worker never had on disk. `name` is only ever used
    in error messages -- nothing about extraction depends on it.

    Index `i` of the result is page number `i + 1`; `page_number` is 1-based in
    the database and this is the only place that conversion happens.

    Pages with no extractable text come back as `""` rather than being skipped,
    so the list index stays aligned with the page number. An image-only slide
    deck is a legitimately empty page, not an error.

    A file that is not a readable PDF raises `ServiceError`, not the underlying
    `PdfminerException`. That translation is load-bearing rather than cosmetic:
    `ServiceError` is what marks a failure **permanent**, and a malformed PDF
    fails identically on every attempt. Left as a library exception it would be
    classified transient and retried three times, turning one deterministic
    problem into three failed runs that look like flakiness.

    **Do not "simplify" this by letting the library exception through.** It reads
    like a wrapper that only changes the message. What it actually does is carry
    the permanent/transient decision to the one place that can make it: the retry
    classifier upstream sees an exception type and nothing else, and cannot tell a
    malformed PDF from a dropped socket. Any deterministic library failure added
    here needs the same treatment.
    """
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            # extract_text() returns None for a page with no text objects.
            return [page.extract_text() or "" for page in pdf.pages]
    except PdfminerException as error:
        raise ServiceError(f"could not read {name} as a PDF: {error}") from error


def extract_pages_in_subprocess(data: bytes, *, name: str) -> list[str]:
    """The same thing, in a fresh interpreter. This is what `verify` calls.

    Verification that reuses pages already in memory can only prove that a list
    equals itself. Extracting again in a process that shares nothing is what
    makes the check able to fail: it re-reads the file from disk, re-runs
    pdfplumber, and so catches extraction that is not deterministic across runs
    or across a library version bump -- exactly the failure the `==` pins exist
    to make loud, and the one that would otherwise surface much later as
    highlights landing on the wrong passage.

    The bytes go in over stdin rather than via a temp file. A temp file would
    need a name nothing collides with, a cleanup path that survives the child
    crashing, and on Windows a handle closed before the child can open it -- all
    to hand over bytes this process is already holding.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "app.services.extraction", name],
        input=data,
        capture_output=True,
        cwd=BACKEND_DIR,
        check=True,
    )
    # Binary pipes both ways, so stdout is decoded here rather than by the
    # subprocess machinery: the payload going in is a PDF, and `text=True` would
    # apply an encoding to it.
    return json.loads(completed.stdout.decode("utf-8"))


if __name__ == "__main__":
    # Read and written as bytes, and ASCII-escaped by default: these pipes carry
    # data, and the Windows console code page (cp1252) cannot encode characters
    # this corpus actually contains, such as U+2022.
    pages = extract_pages(sys.stdin.buffer.read(), name=sys.argv[1])
    sys.stdout.buffer.write(json.dumps(pages).encode("utf-8"))
