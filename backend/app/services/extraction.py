"""PDF text extraction.

One job: turn a PDF into one string per page. Those strings are the coordinate
system every `chunks.char_start`/`char_end` indexes into, so this module has a
rule it does not break:

**The text is returned exactly as pdfplumber produced it, with one substitution:
`normalise_page_text` below. No stripping, no whitespace collapsing, nothing
else.**

Any transformation applied here becomes part of the coordinate system, and
`verify` re-extracts in a fresh process and compares byte for byte. A cleanup
step that lives on only one of those two paths turns every offset into a lie.
That is why the one substitution there is lives in a named function called from
`extract_pages` itself, which is the single door both paths go through.
"""

import io
import json
import subprocess
import sys

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException

from app.core.paths import BACKEND_DIR
from app.services.errors import ServiceError


def normalise_page_text(text: str) -> str:
    """Replace NUL with U+FFFD. This is the only normalisation this module does.

    pdfminer emits a character per glyph, and a glyph whose font carries no
    usable mapping comes out as whatever that font's encoding says -- twelve
    times, on pages 6 and 7 of lecture 16 of the eval corpus, as U+0000.
    Postgres `text` cannot store U+0000 at all, so those twelve characters
    failed the whole document's chunk insert with a driver-level error that
    named no page and no character.

    **Same length in, same length out**, which is the reason it is a substitution
    and not a strip. Every `chunks.char_start`/`char_end` indexes into the string
    this module returns, so deleting a character would slide every offset after
    it on that page -- silently, and only on the pages that happen to contain
    one. A one-for-one swap cannot move an offset, and pages with no NUL are
    returned unchanged, so nothing already ingested is affected.

    U+FFFD rather than a space because it is the character pdfminer itself
    already emits for an unreadable glyph -- twice, in lecture 9 of the same
    corpus, which arrived with no NUL and no complaint. So this is not a
    convention being invented here, and a reader who meets one has the same
    thing to conclude either way: a glyph was here and could not be read. A
    space would instead invent a word boundary the page does not have.
    """
    # Written as escapes on purpose: one of these two characters is invisible and
    # the other is indistinguishable from a genuinely broken file encoding.
    return text.replace("\x00", "\ufffd")


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
            return [
                normalise_page_text(page.extract_text() or "") for page in pdf.pages
            ]
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
