"""Ingestion: pages in, chunk rows out.

This module owns the one place content is produced. `plan_chunks` slices each
page string with the offsets `chunk_page` returned, so a `PlannedChunk`'s
`content` and its `(char_start, char_end)` cannot disagree -- there is no other
code path that builds content.
"""

from dataclasses import dataclass

from app.services.chunking import chunk_page


@dataclass(frozen=True)
class PlannedChunk:
    """One row-to-be of `chunks`, before it has a document to belong to.

    `page_number` is 1-based, matching the column. `char_start`/`char_end` index
    into that page's text only. `chunk_index` runs across the whole document
    rather than restarting per page, so chunks sort into reading order with one
    ORDER BY.
    """

    page_number: int
    chunk_index: int
    char_start: int
    char_end: int
    content: str


def plan_chunks(pages: list[str]) -> list[PlannedChunk]:
    """Chunk every page, in document order.

    Empty pages contribute nothing but do not disturb numbering: page numbers
    come from the list index, so an image-only slide leaves a gap in the chunk
    record without shifting the pages after it.
    """
    planned: list[PlannedChunk] = []
    chunk_index = 0

    for page_offset, text in enumerate(pages):
        for char_start, char_end in chunk_page(text):
            planned.append(
                PlannedChunk(
                    page_number=page_offset + 1,
                    chunk_index=chunk_index,
                    char_start=char_start,
                    char_end=char_end,
                    # The only place content is ever produced.
                    content=text[char_start:char_end],
                )
            )
            chunk_index += 1

    return planned
