from datetime import datetime

from sqlalchemy import DateTime, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every model. Alembic reads `Base.metadata`."""


class CreatedAt:
    """Invariant 1, first half: every table records when the row was inserted.

    A mixin rather than four copies, because the one way this invariant breaks
    is someone adding a table and forgetting. `now()` is a server default so the
    value comes from Postgres, not from whichever machine ran the insert.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
