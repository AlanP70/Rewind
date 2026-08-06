import uuid

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt

# The one hardcoded user, inserted by migration 0002. Auth is Phase 7; until then
# every row in the system is owned by this id. Restated here rather than imported
# from the migration: migrations are a historical record of how the schema got to
# where it is, and the application never imports them.
SEED_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class User(Base, CreatedAt):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
