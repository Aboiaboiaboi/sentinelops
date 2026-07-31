"""Comparing one scan against the one before it.

Pure functions over two scans. Kept out of scan_service because none of this
touches the database — which is what lets every rule below be tested against
two constructed objects rather than a fixture.

The rules that matter are the refusals. A comparison that quietly renders a
misleading delta is worse than one that declines to render at all, and there
are two ways a delta can lie: the rubric changed between the scans, or a
category stopped being assessed rather than getting worse.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models import Scan, ScanStatus


@dataclass(frozen=True)
class CategoryDelta:
    """One category's movement between two scans.

    `delta` is None when either side did not report. That is not zero and not a
    drop: a category can stop being assessed — every check skipped, or its
    scanner failing — and showing that as a loss of its full weight would
    accuse the repository of a regression it did not have.
    """

    category: str
    previous: int | None
    current: int | None
    delta: int | None


@dataclass(frozen=True)
class CheckChange:
    """A single check whose outcome moved."""

    id: str
    title: str
    category: str
    previous_outcome: str
    current_outcome: str

    @property
    def is_regression(self) -> bool:
        return self.current_outcome == "failed" and self.previous_outcome != "failed"

    @property
    def is_improvement(self) -> bool:
        return self.previous_outcome == "failed" and self.current_outcome != "failed"


@dataclass(frozen=True)
class ScanComparison:
    previous_scan_id: uuid.UUID | None
    previous_created_at: datetime | None
    previous_score: int | None
    #: False when the two scans cannot honestly be compared; `reason` says why.
    comparable: bool
    reason: str | None
    score_delta: int | None
    categories: list[CategoryDelta]
    checks: list[CheckChange]


NOTHING_TO_COMPARE = ScanComparison(
    previous_scan_id=None,
    previous_created_at=None,
    previous_score=None,
    comparable=False,
    reason=None,
    score_delta=None,
    categories=[],
    checks=[],
)


def _outcomes(check_results: list[Any] | None) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in (check_results or []) if "id" in entry}


def compare(previous: Scan | None, current: Scan) -> ScanComparison:
    """What changed between `previous` and `current`.

    Returns NOTHING_TO_COMPARE when there is no honest comparison to draw —
    no earlier scan, or one of the two never produced a score.
    """
    if previous is None:
        return NOTHING_TO_COMPARE
    if current.status is not ScanStatus.COMPLETED or previous.status is not ScanStatus.COMPLETED:
        return NOTHING_TO_COMPARE

    # A score is only meaningful against the rubric that produced it. Weights
    # change between scoring versions, so a delta across versions measures our
    # own change and attributes it to the user's repository.
    if previous.scoring_version != current.scoring_version:
        return ScanComparison(
            previous_scan_id=previous.id,
            previous_created_at=previous.created_at,
            previous_score=previous.score,
            comparable=False,
            reason=(
                f"These scans were scored under different rubrics "
                f"({previous.scoring_version} and {current.scoring_version}), so the difference "
                "between them would measure a change in SentinelOps rather than in the repository."
            ),
            score_delta=None,
            categories=[],
            checks=[],
        )

    previous_scores: dict[str, int] = previous.category_scores or {}
    current_scores: dict[str, int] = current.category_scores or {}

    categories = [
        CategoryDelta(
            category=category,
            previous=previous_scores.get(category),
            current=current_scores.get(category),
            delta=(
                current_scores[category] - previous_scores[category]
                if category in previous_scores and category in current_scores
                else None
            ),
        )
        for category in sorted(set(previous_scores) | set(current_scores))
    ]

    return ScanComparison(
        previous_scan_id=previous.id,
        previous_created_at=previous.created_at,
        previous_score=previous.score,
        comparable=True,
        reason=None,
        score_delta=(
            current.score - previous.score
            if current.score is not None and previous.score is not None
            else None
        ),
        categories=categories,
        checks=_changed_checks(previous, current),
    )


def _changed_checks(previous: Scan, current: Scan) -> list[CheckChange]:
    """Checks whose outcome moved, worst news first.

    Only checks present in *both* scans are compared. A check that exists only
    in the newer one was added by us, not changed by the repository, and
    presenting it as a change would credit or blame somebody for a release of
    SentinelOps.
    """
    before = _outcomes(previous.check_results)
    after = _outcomes(current.check_results)

    changes = [
        CheckChange(
            id=check_id,
            title=entry.get("title", check_id),
            category=entry.get("category", ""),
            previous_outcome=before[check_id]["outcome"],
            current_outcome=entry["outcome"],
        )
        for check_id, entry in after.items()
        if check_id in before and before[check_id]["outcome"] != entry["outcome"]
    ]

    # Regressions first: the thing somebody needs to see is what broke.
    def order(change: CheckChange) -> tuple[int, str]:
        if change.is_regression:
            return (0, change.id)
        if change.is_improvement:
            return (1, change.id)
        return (2, change.id)

    return sorted(changes, key=order)
