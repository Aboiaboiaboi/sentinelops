import uuid

from pydantic import BaseModel, ConfigDict, computed_field

from app.models.scan import CategoryStatus, ScanStatus
from app.schemas.common import UtcDatetime
from app.services.scoring_service import CATEGORY_WEIGHTS


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

    # Points each completed category earned. Empty while a scan is running, and
    # for scans that predate the field. Without it a client can only assume a
    # completed category scored full marks, which made the chart disagree with
    # the total — a category worth 20 that lost 3 still drew a full bar.
    category_scores: dict[str, int]

    created_at: UtcDatetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def category_max_scores(self) -> dict[str, int]:
        """The cap for each category, so a client needs no copy of the weights.

        Computed rather than stored: the caps are a property of the rubric, not
        of one scan, and duplicating them into every row would mean a weight
        change silently disagreeing with itself across old and new scans.
        `scoring_version` is what records which rubric produced a given score.
        """
        return dict(CATEGORY_WEIGHTS)
