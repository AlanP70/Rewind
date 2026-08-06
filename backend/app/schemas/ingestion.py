"""Shapes passed between the ingestion layers.

`PlannedChunk` lives here rather than in `services/` because `repositories/`
takes one as an argument, and a repository importing from a service inverts the
layering (`api/` -> `services/` -> `repositories/`). `schemas/` is the peer
directory both may depend on.
"""

from dataclasses import dataclass


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
