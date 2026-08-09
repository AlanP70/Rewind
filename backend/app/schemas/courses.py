"""Response bodies for the course routes."""

import uuid
from datetime import date

from pydantic import BaseModel


class CourseSummary(BaseModel):
    """One entry in the upload form's course picker.

    Term dates are included because `name` alone does not disambiguate the same
    course taken twice, which is the case Phase 3's dating exists to handle.

    `from_attributes` lets FastAPI build this straight from the SQLAlchemy row.
    That is safe here only because every field on the model is one a client may
    see -- it is not a licence to return ORM objects generally.
    """

    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    code: str | None
    term: str | None
    starts_on: date
    ends_on: date
