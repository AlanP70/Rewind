"""Importing every model here is what populates `Base.metadata`.

Alembic's env.py imports this module and nothing else, so a model that is not
listed here is invisible to autogenerate.
"""

from app.models.base import Base
from app.models.chunk import EMBEDDING_DIMENSIONS, Chunk
from app.models.course import Course
from app.models.document import Document, DocumentKind, DocumentStatus, OccurredAtSource
from app.models.user import User

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "Base",
    "Chunk",
    "Course",
    "Document",
    "DocumentKind",
    "DocumentStatus",
    "OccurredAtSource",
    "User",
]
