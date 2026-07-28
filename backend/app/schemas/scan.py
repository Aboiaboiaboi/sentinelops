import uuid

from pydantic import BaseModel, ConfigDict

from app.models.scan import CategoryStatus, ScanStatus
from app.schemas.common import UtcDatetime


class ScanRead(BaseModel):
    """Shape of GET /scans/{id} — the endpoint the client polls.

    Every field is always present. `score` and `scoring_version` are null until
    the scan finishes rather than being omitted, because the client's type has
    no optional keys and would read a missing key as undefined.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: ScanStatus
    score: int | None
    scoring_version: str | None
    # Category name -> outcome. Typed as the enum so an unrecognised value fails
    # here rather than reaching the client, which uses these strings to pick a
    # bar colour and would silently render nothing for an unknown one.
    category_status: dict[str, CategoryStatus]
    created_at: UtcDatetime
