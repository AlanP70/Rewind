"""split filename dating into read and interpolated

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-10

ROADMAP's Phase 3 named three sources: `parsed_syllabus`, `inferred_filename`,
`manual`. This adds a fourth, `filename_date`, and the reason is the whole point
of the column.

`2024-09-14.pdf` is a date *read* off a filename. `Lecture 07.pdf` is a date
*interpolated* from an ordinal against the course's term bounds, and it goes
systematically wrong the moment a term contains a reading week or runs two
lectures in one week. The first is very nearly a fact; the second is a guess.
Storing both as `inferred_filename` means the UI cannot tell them apart, which
defeats `occurred_at_source` exactly where honesty matters most -- see
ARCHITECTURE's note that this column exists so the UI can be honest about
confidence.

`inferred_filename` keeps its name and narrows to mean the interpolated case
only, rather than being renamed to something like `filename_ordinal`. Nothing has
ever written it -- Phase 3 is the first dating code -- so there is no data to
migrate, and the name is already load-bearing in ROADMAP and ARCHITECTURE.

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_documents_occurred_at_source"

OLD = "occurred_at_source IN ('parsed_syllabus', 'inferred_filename', 'manual')"
NEW = (
    "occurred_at_source IN "
    "('parsed_syllabus', 'filename_date', 'inferred_filename', 'manual')"
)


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "documents", type_="check")
    op.create_check_constraint(CONSTRAINT, "documents", NEW)


def downgrade() -> None:
    # Fails if any row has been dated `filename_date`, deliberately. The
    # alternative -- nulling those rows to let the constraint back on -- would
    # silently discard dates, and `ck_documents_occurred_at_has_source` means it
    # would have to discard the `occurred_at` beside each one too.
    op.drop_constraint(CONSTRAINT, "documents", type_="check")
    op.create_check_constraint(CONSTRAINT, "documents", OLD)
