import uuid

from pydantic import BaseModel, ConfigDict, computed_field

from app.models.scan import SCAN_ERROR_HINTS, CategoryStatus, ScanStatus
from app.scanners.base import CheckOutcome
from app.schemas.common import UtcDatetime
from app.services.scoring_service import CATEGORY_WEIGHTS


class CheckResultRead(BaseModel):
    """One check a scan performed. Shape of GET /scans/{id}/checks.

    `reason` is present only for a skipped check and is required there — a
    skip with no explanation is the same dead end as the silence that check
    results were introduced to replace.
    """

    id: str
    category: str
    title: str
    outcome: CheckOutcome
    reason: str | None = None


class CategoryDeltaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str
    previous: int | None
    current: int | None
    #: Null when either side did not report — which is different from zero, and
    #: different again from a drop. A category can stop being assessed.
    delta: int | None


class CheckChangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    category: str
    previous_outcome: CheckOutcome
    current_outcome: CheckOutcome


class ScanComparisonRead(BaseModel):
    """Shape of GET /scans/{id}/comparison.

    `previous_scan_id` null means there was nothing to compare against — the
    first scan of a project, or no earlier one that completed. `comparable`
    false with a `reason` means there *is* an earlier scan but the difference
    would mislead; the reason is written to be shown to the user.
    """

    model_config = ConfigDict(from_attributes=True)

    previous_scan_id: uuid.UUID | None
    previous_created_at: UtcDatetime | None
    previous_score: int | None
    comparable: bool
    reason: str | None
    score_delta: int | None
    categories: list[CategoryDeltaRead]
    checks: list[CheckChangeRead]


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

    # Why a failed scan failed. Null for anything that has not failed, and for
    # scans that failed before this was recorded.
    error_category: str | None
    error_detail: str | None

    # The commit this scan looked at. All null together when the checkout had
    # no HEAD — an empty repository — or for scans that predate the field.
    # Present rather than omitted, same reason as `score`.
    commit_sha: str | None
    commit_message: str | None
    commit_author: str | None
    committed_at: UtcDatetime | None

    created_at: UtcDatetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_hint(self) -> str | None:
        """What to try, derived from the category rather than stored.

        The advice is a property of the failure kind, not of one scan, so
        keeping it out of the row means improving the wording does not need a
        migration and cannot leave old scans quoting stale advice.
        """
        if self.error_category is None:
            return None
        return SCAN_ERROR_HINTS.get(self.error_category)

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
