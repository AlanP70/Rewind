"""storage_path becomes storage_key

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-08

Phase 1 stored a repo-relative filesystem path here and said in ROADMAP that this
column becomes a storage key when Supabase Storage lands. It has. The column now
holds `{user_id}/{filename}`, addressed through `app/core/storage.py`, and a
column named `_path` holding that would mislead every later reader.

A pure rename: no type change, no data transformation. Existing rows keep their
old repo-relative values, which are not valid keys -- there were none outside one
development database when this ran, and re-ingesting rewrites them. If this is
ever applied somewhere with rows that matter, delete them first; a path is not a
key and no expression turns one into the other.

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("documents", "storage_path", new_column_name="storage_key")
    # The constraint name embeds the column name. Left alone it would read
    # `..._storage_path` over a column called `storage_key`, which is the same
    # confusion one level down where it is harder to notice.
    op.execute(
        "ALTER TABLE documents RENAME CONSTRAINT "
        "uq_documents_user_id_storage_path TO uq_documents_user_id_storage_key"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE documents RENAME CONSTRAINT "
        "uq_documents_user_id_storage_key TO uq_documents_user_id_storage_path"
    )
    op.alter_column("documents", "storage_key", new_column_name="storage_path")
