"""Embedding: chunk text in, vectors into `chunks.embedding`.

**This service commits, unlike the rest of them.** Everywhere else the caller
owns the transaction; here each batch is committed as it succeeds, and that is
the entire design. A semester's worth of chunks embedded under one transaction
would discard every vector already paid for the moment one request fails, and
there is nothing atomic to protect: a chunk's embedding depends on that chunk
alone.

What partial failure looks like, therefore:

- Batches that succeeded are committed and stay committed.
- The document is moved to `failed`, in its own transaction, so a half-filled
  embedding column is never left sitting under a `processing` status.
- Re-running picks up only chunks with a null embedding, so nothing is billed
  twice and the run resumes where it stopped.
"""

import uuid
from dataclasses import dataclass

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Chunk, DocumentStatus
from app.models.chunk import EMBEDDING_DIMENSIONS
from app.repositories import chunks as chunks_repo
from app.repositories import documents as documents_repo
from app.services.errors import ServiceError

MODEL = "text-embedding-3-small"

# The API accepts far more per request; 100 is chosen for commit granularity
# rather than throughput. A failure costs at most this many chunks of progress.
BATCH_SIZE = 100

# Characters per token. A rough divisor, not a tokeniser -- good enough to answer
# "roughly what will this run cost" without adding tiktoken as a dependency, and
# labelled as an estimate everywhere it is shown.
CHARS_PER_TOKEN = 4

# USD per million tokens for MODEL. A published rate, not something the code can
# discover: confirm it against OpenAI's pricing page before trusting a large
# estimate.
USD_PER_MILLION_TOKENS = 0.02


@dataclass(frozen=True)
class EmbeddingEstimate:
    chunk_count: int
    estimated_tokens: int
    estimated_usd: float


@dataclass(frozen=True)
class EmbeddingResult:
    embedded: int
    remaining: int
    status: str


def estimate(chunks: list[Chunk]) -> EmbeddingEstimate:
    """What this run will roughly cost, before any of it is spent."""
    characters = sum(len(chunk.content) for chunk in chunks)
    tokens = characters // CHARS_PER_TOKEN
    return EmbeddingEstimate(
        chunk_count=len(chunks),
        estimated_tokens=tokens,
        estimated_usd=tokens / 1_000_000 * USD_PER_MILLION_TOKENS,
    )


async def embed_query(query: str) -> list[float]:
    """One search query as a vector, using the model that embedded the chunks.

    Lives here rather than in `services/search.py` for one reason: `MODEL` is the
    only thing that makes a query vector and a chunk vector comparable. A search
    module that named its own model would still return results -- ranked by the
    distance between two unrelated coordinate systems, which is nonsense that
    looks exactly like poor retrieval and would be debugged as if it were.
    """
    if not settings.openai_api_key:
        raise ServiceError("OPENAI_API_KEY is not set, so a query cannot be embedded")

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.embeddings.create(model=MODEL, input=[query])
    vector = response.data[0].embedding

    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ServiceError(
            f"{MODEL} returned {len(vector)} dimensions, expected {EMBEDDING_DIMENSIONS}"
        )
    return vector


async def pending_chunks(session: AsyncSession, document_id: uuid.UUID) -> list[Chunk]:
    return await chunks_repo.list_unembedded(session, document_id)


async def embed_document(
    session: AsyncSession, *, user_id: uuid.UUID, document_id: uuid.UUID
) -> EmbeddingResult:
    """Embed every chunk of a document that does not have a vector yet.

    Commits per batch. Raises `ServiceError` after marking the document `failed`
    if a request fails; whatever was committed before that stays committed.
    """
    document = await documents_repo.get(session, document_id, user_id)
    if document is None:
        raise ServiceError(f"no document {document_id} for this user")

    pending = await chunks_repo.list_unembedded(session, document_id)
    if not pending:
        # Nothing to do, but the status may still be stale from an earlier run
        # that failed after its last successful batch. Checked before the key,
        # so a fully embedded document can be settled without one.
        return await _finish(session, document_id, embedded=0)

    if not settings.openai_api_key:
        raise ServiceError(
            f"OPENAI_API_KEY is not set and {len(pending)} chunk(s) still need embedding"
        )

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    embedded = 0

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        try:
            response = await client.embeddings.create(
                model=MODEL, input=[chunk.content for chunk in batch]
            )
            # The API returns results in request order, but it also returns an
            # explicit index. Trusting position over that index is the kind of
            # assumption that silently attaches the wrong vector to the wrong
            # chunk, which no later check would catch.
            for item in response.data:
                if len(item.embedding) != EMBEDDING_DIMENSIONS:
                    raise ServiceError(
                        f"{MODEL} returned {len(item.embedding)} dimensions, "
                        f"expected {EMBEDDING_DIMENSIONS}"
                    )
                batch[item.index].embedding = item.embedding
            await session.commit()
            embedded += len(batch)
        except Exception as error:
            await session.rollback()
            await documents_repo.set_status(session, document_id, DocumentStatus.FAILED)
            await session.commit()
            remaining = await chunks_repo.count_unembedded(session, document_id)
            raise ServiceError(
                f"embedding failed after {embedded} chunk(s); {remaining} still "
                f"unembedded and the document is marked failed. "
                f"Re-run `embed {document_id}` to resume. Cause: {error}"
            ) from error

    return await _finish(session, document_id, embedded=embedded)


async def _finish(
    session: AsyncSession, document_id: uuid.UUID, *, embedded: int
) -> EmbeddingResult:
    """Set the terminal status from what is actually in the table.

    Counted rather than inferred from "the loop finished without raising":
    `ready` is a claim that the document is searchable, and the only evidence for
    it is that no chunk is missing a vector.
    """
    remaining = await chunks_repo.count_unembedded(session, document_id)
    status = DocumentStatus.READY if remaining == 0 else DocumentStatus.PROCESSING
    await documents_repo.set_status(session, document_id, status)
    await session.commit()
    return EmbeddingResult(embedded=embedded, remaining=remaining, status=status)
