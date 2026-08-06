"""Chunking: a page string in, index pairs out.

**This module never returns text.** `chunk_page` returns `(start, end)` pairs
into the string it was given, and the caller produces content by slicing that
same string. That is what makes `text[start:end] == content` true by
construction rather than by two code paths agreeing — see ROADMAP.md, Phase 1.

Indices are page-local: they index into one page's extracted text, never into
the whole document.
"""

import re

# Tuned against a real MIT 6.006 lecture PDF, not picked in the abstract.
TARGET_SIZE = 1000
OVERLAP = 150

# A boundary is only accepted once the chunk is this full. Measured, not guessed:
# on the 6.006 DFS lecture, 0.5 lets a single stray period early in the window win
# the sentence tier and emit a 236-character chunk next to 1000-character ones.
# 0.7 removes the runt and drops the document from 10 chunks to 9; 0.75 and above
# start producing a different runt (347). Re-measure if TARGET_SIZE changes.
MIN_FILL = 0.7

# Separators in descending order of how much we would like to break there. A
# boundary from an earlier pattern beats one from a later pattern even if it
# falls further back in the window.
#
# Sentence ranks above a bare line break on purpose, and it matters which way
# round. In hard-wrapped prose a line break lands mid-sentence and is a bad
# split; in slide-style notes -- which is what the 6.006 lectures are -- there
# are almost no sentence-ending periods at all, so the sentence tier finds
# nothing and the line tier takes over. This order is the one that behaves for
# both, which is why a bare line break is a tier rather than the whole strategy.
_SEPARATORS = [
    re.compile(r"\n\s*\n"),  # blank line: a paragraph break
    re.compile(r"[.!?][\"')\]]*\s+"),  # sentence end, closing quotes included
    re.compile(r"\n"),  # line break: the unit slide decks are written in
    re.compile(r"[ \t]+"),  # last resort before cutting mid-word
]


def _find_boundary(text: str, start: int, hi: int, floor: int) -> int:
    """Index to split at: the last acceptable boundary at or before `hi`.

    Only boundaries at or after `floor` count -- otherwise a lone paragraph break
    near the top of the window would produce a 40-character chunk. Falls back to
    `hi` (a hard cut) when no separator qualifies, which is what happens to a
    page of unbroken text.
    """
    for pattern in _SEPARATORS:
        best = None
        for match in pattern.finditer(text, start, hi):
            # Split *after* the separator, so it stays with the chunk it ends.
            if floor <= match.end() <= hi:
                best = match.end()
        if best is not None:
            return best
    return hi


def chunk_page(
    text: str, target_size: int = TARGET_SIZE, overlap: int = OVERLAP
) -> list[tuple[int, int]]:
    """Split one page's text into overlapping `(char_start, char_end)` spans.

    Walks the page greedily: each chunk ends at the best boundary inside the
    target window, and the next chunk starts `overlap` characters before that
    end so an idea straddling a boundary survives whole in at least one chunk.

    A page with no text yields no chunks. `char_end > char_start` holds for every
    span returned, matching the check constraint on `chunks`.
    """
    if not text.strip():
        return []

    spans: list[tuple[int, int]] = []
    start = 0
    length = len(text)

    while start < length:
        window_end = start + target_size
        if window_end >= length:
            spans.append((start, length))
            break

        # The floor guarantees forward progress: every chunk advances at least
        # (MIN_FILL * target_size - overlap) characters, so the loop cannot stall.
        end = _find_boundary(text, start, window_end, floor=start + int(target_size * MIN_FILL))
        spans.append((start, end))
        start = end - overlap

    return spans
