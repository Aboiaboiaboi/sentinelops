"""Tests for the scoring rule.

The rule that needs the most pinning down is what an unreported category costs.
It contributes nothing rather than shrinking the denominator, so a scan that
assessed half the rubric cannot look like a thorough one.
"""

import pytest

from app.models import SCAN_CATEGORIES
from app.scanners.base import ScanFinding, Severity
from app.services.scoring_service import (
    CATEGORY_WEIGHTS,
    MAX_SCORE,
    SCORING_VERSION,
    category_max_score,
    score_by_category,
    score_category,
    score_scan,
)


def _finding(category: str, impact: int) -> ScanFinding:
    return ScanFinding(
        category=category,
        severity=Severity.MEDIUM,
        title="t",
        description="d",
        recommendation="r",
        score_impact=impact,
    )


def _all(status: str) -> dict[str, str]:
    return dict.fromkeys(SCAN_CATEGORIES, status)


class TestWeights:
    def test_weights_sum_to_the_maximum(self) -> None:
        assert sum(CATEGORY_WEIGHTS.values()) == MAX_SCORE

    def test_every_scanner_category_has_a_weight(self) -> None:
        """A category with no weight would be scanned and then silently ignored."""
        assert set(CATEGORY_WEIGHTS) == set(SCAN_CATEGORIES)

    def test_weights_match_what_the_frontend_ships(self) -> None:
        """The client sizes its chart bars from its own copy of this table.
        They disagreeing would make the bars not add up to the score."""
        assert CATEGORY_WEIGHTS == {
            "security": 25,
            "reliability": 20,
            "architecture": 20,
            "deployment": 15,
            "observability": 10,
            "scalability": 10,
        }

    def test_unknown_category_has_no_weight(self) -> None:
        assert category_max_score("teleportation") == 0


class TestScoreCategory:
    def test_no_findings_scores_full_weight(self) -> None:
        """Nothing found wrong is full marks, not zero."""
        assert score_category("security", []) == 25

    def test_findings_deduct_their_impact(self) -> None:
        assert score_category("security", [_finding("security", 10)]) == 15

    def test_multiple_findings_accumulate(self) -> None:
        findings = [_finding("security", 10), _finding("security", 6)]

        assert score_category("security", findings) == 9

    def test_cannot_go_negative(self) -> None:
        """Otherwise one bad category eats another's marks."""
        assert score_category("scalability", [_finding("scalability", 500)]) == 0


class TestScoreScan:
    def test_a_clean_repository_scores_full_marks(self) -> None:
        assert score_scan([], _all("completed")) == MAX_SCORE

    def test_findings_reduce_the_total(self) -> None:
        findings = [_finding("security", 10), _finding("deployment", 5)]

        assert score_scan(findings, _all("completed")) == MAX_SCORE - 15

    def test_a_failed_category_costs_its_whole_weight(self) -> None:
        """Not a reduced denominator — the scan genuinely does not know whether
        that part is sound."""
        status = _all("completed") | {"security": "failed"}

        assert score_scan([], status) == MAX_SCORE - 25

    def test_a_pending_category_also_contributes_nothing(self) -> None:
        status = _all("completed") | {"observability": "pending"}

        assert score_scan([], status) == MAX_SCORE - 10

    def test_nothing_reported_scores_zero(self) -> None:
        assert score_scan([], _all("failed")) == 0

    def test_findings_from_a_failed_category_are_ignored(self) -> None:
        """That category's weight is already forfeit. Counting its partial
        findings would deduct twice for the same failure."""
        status = _all("completed") | {"security": "failed"}

        with_findings = score_scan([_finding("security", 20)], status)
        without = score_scan([], status)

        assert with_findings == without

    def test_findings_for_an_unknown_category_are_ignored(self) -> None:
        assert score_scan([_finding("teleportation", 50)], _all("completed")) == MAX_SCORE

    def test_a_missing_category_key_contributes_nothing(self) -> None:
        """A scan whose map predates a new category must not be scored as if
        that category had passed."""
        status = {c: "completed" for c in SCAN_CATEGORIES if c != "scalability"}

        assert score_scan([], status) == MAX_SCORE - 10

    @pytest.mark.parametrize("status", ["completed", "failed", "pending"])
    def test_score_is_always_within_range(self, status: str) -> None:
        findings = [_finding(c, 999) for c in SCAN_CATEGORIES]

        assert 0 <= score_scan(findings, _all(status)) <= MAX_SCORE

    def test_security_alone_caps_the_rest_at_75(self) -> None:
        """The consequence of scoring out of a full 100: until a security
        scanner exists, a flawless repository tops out at grade C."""
        status = _all("completed") | {"security": "failed"}

        assert score_scan([], status) == 75


class TestScoreByCategory:
    """Per-category points, so a client can show what a category actually
    scored instead of assuming a completed one scored full marks."""

    def test_reports_each_completed_category(self) -> None:
        scores = score_by_category([], _all("completed"))

        assert scores == CATEGORY_WEIGHTS

    def test_deductions_apply_to_the_right_category(self) -> None:
        scores = score_by_category([_finding("architecture", 3)], _all("completed"))

        assert scores["architecture"] == 17
        assert scores["security"] == 25

    def test_omits_categories_that_did_not_report(self) -> None:
        """Absent rather than zero. A zero reads as "assessed and terrible"
        rather than "not assessed"."""
        status = _all("failed") | {"architecture": "completed"}

        assert score_by_category([], status) == {"architecture": 20}

    def test_totals_match_score_scan(self) -> None:
        """The two must never disagree — the chart is drawn from one and the
        headline number from the other."""
        findings = [_finding("architecture", 3), _finding("security", 10)]
        status = _all("completed") | {"deployment": "failed"}

        assert sum(score_by_category(findings, status).values()) == score_scan(findings, status)


class TestScoringVersion:
    def test_is_recorded_so_old_scores_stay_interpretable(self) -> None:
        """Pinned deliberately. Changing the rubric without changing this string
        makes every old score silently incomparable to every new one, and
        comparison has no way to know it should decline. v2 re-cut security's 25
        points across eight checks when the real tools replaced the regexes."""
        assert SCORING_VERSION == "v2"
