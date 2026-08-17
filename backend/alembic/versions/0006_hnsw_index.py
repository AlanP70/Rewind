"""index chunks.embedding for cosine nearest-neighbour search

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-16

**The operator class must match the operator the query uses.** `nearest_chunks`
orders by `<=>`, which is cosine distance, so this is `vector_cosine_ops`. An
index built for `vector_l2_ops` or `vector_ip_ops` is not wrong in any way
Postgres will tell you about -- it is simply never chosen for this query, and the
only symptom is that adding an index made no difference. That is a sentence this
phase is going to have to say honestly at least once, so it must not be reachable
by a typo.

HNSW rather than IVFFlat: IVFFlat has to be built against existing rows and
rebuilt as the table grows, and its recall depends on a list count chosen from a
row estimate this corpus does not have yet. HNSW has no such build-time
dependency, which matters for a table that a student adds to one document at a
time.

Default `m` and `ef_construction`. Tuning them against 216 chunks would be
fitting parameters to a corpus two orders of magnitude smaller than the one they
would run on.

Not `CONCURRENTLY`: Alembic runs a migration inside a transaction and
`CREATE INDEX CONCURRENTLY` cannot. The cost is that this locks `chunks` against
writes while it builds, which at present is a table of a few hundred rows on a
single-user application. The first deploy where that lock is felt is the one that
should split this out and run it outside the migration.

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_chunks_embedding_hnsw"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX {INDEX} ON chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
