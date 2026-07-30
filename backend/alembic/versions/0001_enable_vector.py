"""enable vector

Revision ID: 0001
Revises:
Create Date: 2026-07-29

Its entire job is enabling pgvector. Doing that as the first migration, with
nothing else in it, is how we find out whether local Postgres and Supabase agree
about where the extension lands before there is anything depending on it.

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
