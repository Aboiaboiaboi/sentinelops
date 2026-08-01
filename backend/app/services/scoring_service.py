"""Turning findings into a production-readiness score.

Owns the category weights — the single source of truth for them. The frontend
ships the same table today so it can size its chart bars, and the intention is
that the API eventually returns each category's cap so that copy can be deleted.

The scoring rule, decided during planning:

  * Every category is scored out of its own weight, and the weights sum to 100.
  * A category's score is its weight minus the impact of its findings, floored
    at zero. A category nobody found anything wrong with scores full marks.
  * A category that did not report contributes **nothing**. Not a reduced
    denominator — a scan that only assessed half the rubric genuinely does not
    know whether the other half is sound, and inflating the score to hide that
    would make a partial scan indistinguishable from a thorough one.

The last point is why a repository cannot score above 75 until the security
scanner exists: security carries a weight of 25, and until something assesses
it, it is honestly unassessed.
"""

from collections.abc import Iterable, Mapping

from app.models import SCAN_CATEGORIES, CategoryStatus
from app.scanners.base import ScanFinding

# Which version of the rubric produced a score. Stored on every scan so an old
# score stays interpretable after the weights change — without it, comparing two
# scans from either side of a change is meaningless.
#
# v2: security's 25 points were re-cut across eight checks when Gitleaks, Trivy
# and Semgrep replaced part of the regex baseline. The category weights below
# did not move; what a security finding costs did. Comparison correctly refuses
# to show a delta across this boundary — the difference would measure a change
# in SentinelOps, not in the repository.
SCORING_VERSION = "v2"

CATEGORY_WEIGHTS: Mapping[str, int] = {
    "security": 25,
    "reliability": 20,
    "architecture": 20,
    "deployment": 15,
    "observability": 10,
    "scalability": 10,
}

MAX_SCORE = 100

# Checked at import rather than trusted. A weight table that does not sum to 100
# yields scores nobody can interpret, and one that disagrees with the scanner
# categories silently drops a whole category from the rubric.
assert sum(CATEGORY_WEIGHTS.values()) == MAX_SCORE, "category weights must sum to 100"
assert set(CATEGORY_WEIGHTS) == set(SCAN_CATEGORIES), "weights must cover every scanner category"


def category_max_score(category: str) -> int:
    """The most a category can contribute. Zero for anything unrecognised."""
    return CATEGORY_WEIGHTS.get(category, 0)


def score_category(category: str, findings: Iterable[ScanFinding]) -> int:
    """Score one category from its own findings.

    Floored at zero: a category with more deductions than weight has already
    cost everything it can, and letting it go negative would let one bad
    category eat another's marks.
    """
    deducted = sum(finding.score_impact for finding in findings)
    return max(0, category_max_score(category) - deducted)


def score_by_category(
    findings: Iterable[ScanFinding], category_status: Mapping[str, str]
) -> dict[str, int]:
    """Points each completed category earned.

    Only completed categories appear. A category that did not report has no
    score — not a zero, which would read as "assessed and found terrible"
    rather than "not assessed".
    """
    by_category: dict[str, list[ScanFinding]] = {}
    for finding in findings:
        by_category.setdefault(finding.category, []).append(finding)

    return {
        category: score_category(category, by_category.get(category, ()))
        for category in CATEGORY_WEIGHTS
        if category_status.get(category) == CategoryStatus.COMPLETED.value
    }


def score_scan(findings: Iterable[ScanFinding], category_status: Mapping[str, str]) -> int:
    """Total score out of 100 for a scan.

    Only categories recorded as `completed` contribute. Findings belonging to a
    category that did not complete are ignored — a category that crashed partway
    may have reported some of its problems and none of the rest, so counting
    them would deduct for a category whose weight is already forfeit.
    """
    return sum(score_by_category(findings, category_status).values())
