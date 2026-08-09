"""Tests for what a report says.

No database and no PDF library. `build_report` is pure by design, so these
construct ORM instances in memory and assert on the tree — which is the point
of splitting content from rendering: the renderer is the piece Phase 4 chose by
measurement and may yet replace, and none of these assertions move when it does.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models import CategoryStatus, Finding, Project, Scan, ScanStatus, Severity
from app.scanners.base import CheckOutcome
from app.services.report_service import COMMIT_SUBJECT_LIMIT, build_report, category_label
from app.services.scoring_service import score_to_grade


def make_project(**overrides: object) -> Project:
    return Project(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Checkout service",
        repository_url="https://github.com/acme/checkout",
        **overrides,
    )


def make_scan(**overrides: object) -> Scan:
    """A completed scan where every category reported and nothing was found."""
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "status": ScanStatus.COMPLETED,
        "score": 100,
        "scoring_version": "v2",
        "category_status": {
            category: CategoryStatus.COMPLETED.value
            for category in (
                "security",
                "reliability",
                "architecture",
                "deployment",
                "observability",
                "scalability",
            )
        },
        "category_scores": {
            "security": 25,
            "reliability": 20,
            "architecture": 20,
            "deployment": 15,
            "observability": 10,
            "scalability": 10,
        },
        "check_results": [],
        "name": None,
        "created_at": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        "completed_at": datetime(2026, 8, 1, 12, 4, tzinfo=UTC),
    }
    return Scan(**(defaults | overrides))


def make_finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "scan_id": uuid.uuid4(),
        "category": "security",
        "severity": Severity.HIGH,
        "title": "Hardcoded credential",
        "description": "A token is committed in config.py.",
        "recommendation": "Move it to an environment variable.",
        "score_impact": 5,
    }
    return Finding(**(defaults | overrides))


class TestCleanScan:
    """The happy case: everything reported, nothing found."""

    def test_reports_the_score_and_grade(self) -> None:
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert report.score == 100
        assert report.grade == "A"
        assert report.max_score == 100

    def test_carries_the_project_identity(self) -> None:
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert report.project_name == "Checkout service"
        assert report.repository_url == "https://github.com/acme/checkout"

    def test_every_category_reported(self) -> None:
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert report.reported_categories == 6
        assert report.total_categories == 6
        assert report.complete is True

    def test_categories_run_heaviest_first(self) -> None:
        """Same ordering as the chart, so the PDF and the screen agree."""
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert [row.category for row in report.categories] == [
            "security",
            "architecture",
            "reliability",
            "deployment",
            "observability",
            "scalability",
        ]

    def test_no_findings_means_no_groups(self) -> None:
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert report.findings == ()
        assert report.finding_count == 0

    def test_a_scan_with_no_name_carries_none(self) -> None:
        """A renderer falls back to the timestamp, as the UI does — this does
        not invent a title, because the fallback is a presentation choice."""
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert report.scan_name is None
        assert report.created_at == datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class TestFindings:
    def test_all_four_severities_are_grouped_and_ordered(self) -> None:
        findings = [
            make_finding(severity=Severity.LOW, title="Low", score_impact=1),
            make_finding(severity=Severity.CRITICAL, title="Critical", score_impact=8),
            make_finding(severity=Severity.MEDIUM, title="Medium", score_impact=3),
            make_finding(severity=Severity.HIGH, title="High", score_impact=5),
        ]

        report = build_report(make_scan(), project=make_project(), findings=findings)

        assert len(report.findings) == 1
        assert [item.title for item in report.findings[0].items] == [
            "Critical",
            "High",
            "Medium",
            "Low",
        ]

    def test_severity_orders_by_rank_not_alphabetically(self) -> None:
        """Severity is a StrEnum, so a plain sort puts CRITICAL after HIGH."""
        findings = [
            make_finding(severity=Severity.HIGH, title="High"),
            make_finding(severity=Severity.CRITICAL, title="Critical"),
        ]

        report = build_report(make_scan(), project=make_project(), findings=findings)

        assert report.findings[0].items[0].severity == "CRITICAL"

    def test_equal_severities_order_by_what_they_cost(self) -> None:
        findings = [
            make_finding(severity=Severity.HIGH, title="Cheaper", score_impact=2),
            make_finding(severity=Severity.HIGH, title="Dearer", score_impact=7),
        ]

        report = build_report(make_scan(), project=make_project(), findings=findings)

        assert [item.title for item in report.findings[0].items] == ["Dearer", "Cheaper"]

    def test_groups_follow_the_category_order(self) -> None:
        findings = [
            make_finding(category="scalability", title="Scalability finding"),
            make_finding(category="security", title="Security finding"),
        ]

        report = build_report(make_scan(), project=make_project(), findings=findings)

        assert [group.category for group in report.findings] == ["security", "scalability"]
        assert [group.label for group in report.findings] == ["Security", "Scalability"]

    def test_counts_across_every_group(self) -> None:
        findings = [
            make_finding(category="security"),
            make_finding(category="deployment"),
            make_finding(category="deployment"),
        ]

        report = build_report(make_scan(), project=make_project(), findings=findings)

        assert report.finding_count == 3

    def test_text_is_carried_through_unchanged(self) -> None:
        """Escaping belongs to the renderer, at the boundary of the format it
        is producing. Doing it here would double-escape or escape for the wrong
        format the moment the renderer changes."""
        finding = make_finding(description="Found <script> in app.py & left it there")

        report = build_report(make_scan(), project=make_project(), findings=[finding])

        assert report.findings[0].items[0].description == (
            "Found <script> in app.py & left it there"
        )


class TestPartialScans:
    def test_a_running_scan_has_no_score_and_no_grade(self) -> None:
        scan = make_scan(
            status=ScanStatus.RUNNING,
            score=None,
            scoring_version=None,
            completed_at=None,
            category_status=dict.fromkeys(
                ("security", "reliability"), CategoryStatus.PENDING.value
            ),
            category_scores={},
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert report.score is None
        assert report.grade == "—"
        assert report.completed_at is None
        assert report.complete is False

    def test_a_pending_category_scores_none_rather_than_zero(self) -> None:
        """Zero would read as 'assessed and found terrible'."""
        scan = make_scan(
            category_status={
                "security": CategoryStatus.COMPLETED.value,
                "reliability": CategoryStatus.PENDING.value,
            },
            category_scores={"security": 22},
        )

        report = build_report(scan, project=make_project(), findings=[])
        rows = {row.category: row for row in report.categories}

        assert rows["security"].score == 22
        assert rows["reliability"].score is None
        assert rows["reliability"].reported is False

    def test_a_failed_category_scores_none(self) -> None:
        """One category's sandbox timing out still leaves a completable scan."""
        scan = make_scan(
            category_status={
                "security": CategoryStatus.FAILED.value,
                "reliability": CategoryStatus.COMPLETED.value,
            },
            category_scores={"reliability": 20},
            score=20,
        )

        report = build_report(scan, project=make_project(), findings=[])
        rows = {row.category: row for row in report.categories}

        assert rows["security"].score is None
        assert rows["security"].status == CategoryStatus.FAILED.value
        assert report.reported_categories == 1
        assert report.complete is False

    def test_a_completed_category_with_no_points_falls_back_to_its_cap(self) -> None:
        """What scans predating category_scores look like. Same fallback the
        chart makes, so an old scan reads the same in both places."""
        scan = make_scan(category_scores={})

        report = build_report(scan, project=make_project(), findings=[])
        rows = {row.category: row for row in report.categories}

        assert rows["security"].score == 25

    def test_a_category_with_no_status_is_omitted(self) -> None:
        scan = make_scan(category_status={"security": CategoryStatus.COMPLETED.value, "ghost": ""})

        report = build_report(scan, project=make_project(), findings=[])

        assert [row.category for row in report.categories] == ["security"]

    def test_a_scan_with_no_categories_is_not_complete(self) -> None:
        """`complete` must not be vacuously true — zero of zero reported is not
        a thorough scan."""
        scan = make_scan(category_status={})

        report = build_report(scan, project=make_project(), findings=[])

        assert report.total_categories == 0
        assert report.complete is False


class TestFailedScan:
    def test_carries_the_failure_and_its_hint(self) -> None:
        scan = make_scan(
            status=ScanStatus.FAILED,
            score=None,
            error_category="repository_not_found",
            error_detail="The repository could not be reached.",
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert report.status == "failed"
        assert report.error_category == "repository_not_found"
        assert report.error_hint is not None
        assert "private" in report.error_hint

    def test_a_successful_scan_has_no_hint(self) -> None:
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert report.error_category is None
        assert report.error_hint is None

    def test_an_unrecognised_error_category_yields_no_hint(self) -> None:
        """Rather than raising. A scan row written by a future version must
        still produce a report."""
        scan = make_scan(status=ScanStatus.FAILED, score=None, error_category="quantum_flux")

        report = build_report(scan, project=make_project(), findings=[])

        assert report.error_hint is None


class TestChecks:
    def test_groups_checks_by_category_worst_first(self) -> None:
        scan = make_scan(
            check_results=[
                {
                    "id": "sec-1",
                    "category": "security",
                    "title": "A passing check",
                    "outcome": CheckOutcome.PASSED.value,
                },
                {
                    "id": "sec-2",
                    "category": "security",
                    "title": "A skipped check",
                    "outcome": CheckOutcome.SKIPPED.value,
                    "reason": "No lockfile present.",
                },
                {
                    "id": "sec-3",
                    "category": "security",
                    "title": "A failing check",
                    "outcome": CheckOutcome.FAILED.value,
                },
                {
                    "id": "sec-4",
                    "category": "security",
                    "title": "An errored check",
                    "outcome": CheckOutcome.ERRORED.value,
                    "reason": "No sandbox was configured.",
                },
            ]
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert [item.id for item in report.checks[0].items] == ["sec-3", "sec-4", "sec-2", "sec-1"]

    def test_errored_outranks_skipped(self) -> None:
        """An unrun check is our failure, and burying it would make an
        incomplete scan look thorough."""
        scan = make_scan(
            check_results=[
                {"id": "a", "category": "security", "title": "A", "outcome": "skipped"},
                {"id": "b", "category": "security", "title": "B", "outcome": "errored"},
            ]
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert [item.id for item in report.checks[0].items] == ["b", "a"]

    def test_equal_outcomes_order_by_title_for_a_stable_document(self) -> None:
        """Insertion order depends on which scanner finished first, which is
        not a fact about the repository."""
        scan = make_scan(
            check_results=[
                {"id": "b", "category": "security", "title": "Zebra", "outcome": "passed"},
                {"id": "a", "category": "security", "title": "Aardvark", "outcome": "passed"},
            ]
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert [item.title for item in report.checks[0].items] == ["Aardvark", "Zebra"]

    def test_keeps_the_reason_for_a_skip(self) -> None:
        scan = make_scan(
            check_results=[
                {
                    "id": "a",
                    "category": "security",
                    "title": "A",
                    "outcome": "skipped",
                    "reason": "No lockfile present.",
                }
            ]
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert report.checks[0].items[0].reason == "No lockfile present."

    def test_an_absent_reason_is_none_not_empty(self) -> None:
        scan = make_scan(
            check_results=[{"id": "a", "category": "security", "title": "A", "outcome": "passed"}]
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert report.checks[0].items[0].reason is None

    def test_malformed_entries_are_skipped_rather_than_raising(self) -> None:
        """check_results is JSONB — whatever an older version wrote. A report
        that omits one bad row beats an endpoint that 500s on it."""
        scan = make_scan(
            check_results=[
                "not a map",
                None,
                {"id": "a", "category": "security", "title": "A", "outcome": "passed"},
            ]
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert [item.id for item in report.checks[0].items] == ["a"]

    def test_no_checks_means_no_groups(self) -> None:
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert report.checks == ()


class TestCommitContext:
    def test_absent_when_the_checkout_had_no_head(self) -> None:
        report = build_report(make_scan(), project=make_project(), findings=[])

        assert report.commit is None

    def test_carries_the_commit(self) -> None:
        scan = make_scan(
            commit_sha="0123456789abcdef0123456789abcdef01234567",
            commit_message="Add the health endpoint",
            commit_author="Dana Scully",
            committed_at=datetime(2026, 7, 30, 9, 0, tzinfo=UTC),
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert report.commit is not None
        assert report.commit.short_sha == "0123456"
        assert report.commit.subject == "Add the health endpoint"
        assert report.commit.author == "Dana Scully"

    def test_uses_only_the_first_line_of_the_message(self) -> None:
        scan = make_scan(
            commit_sha="abc1234", commit_message="Subject line\n\nA body paragraph explaining it."
        )

        report = build_report(scan, project=make_project(), findings=[])

        assert report.commit is not None
        assert report.commit.subject == "Subject line"

    def test_truncates_a_very_long_subject(self) -> None:
        scan = make_scan(commit_sha="abc1234", commit_message="x" * 400)

        report = build_report(scan, project=make_project(), findings=[])

        assert report.commit is not None
        assert len(report.commit.subject) == COMMIT_SUBJECT_LIMIT
        assert report.commit.subject.endswith("…")

    def test_present_when_only_the_sha_was_read(self) -> None:
        """A scan that recorded a sha and failed to read the author still
        identifies which code was scanned."""
        scan = make_scan(commit_sha="abc1234", commit_message=None, commit_author=None)

        report = build_report(scan, project=make_project(), findings=[])

        assert report.commit is not None
        assert report.commit.subject == ""
        assert report.commit.author is None


class TestGrades:
    @pytest.mark.parametrize(
        ("score", "grade"),
        [(100, "A"), (90, "A"), (89, "B"), (80, "B"), (79, "C"), (70, "C"), (69, "D"), (60, "D")],
    )
    def test_matches_the_frontend_thresholds(self, score: int, grade: str) -> None:
        assert score_to_grade(score) == grade

    @pytest.mark.parametrize("score", [59, 42, 0])
    def test_below_sixty_fails(self, score: int) -> None:
        assert score_to_grade(score) == "F"

    def test_no_score_is_not_a_failing_grade(self) -> None:
        """A scan that never finished has no grade, rather than an F."""
        assert score_to_grade(None) == "—"


class TestCategoryLabels:
    def test_labels_the_six_known_categories(self) -> None:
        assert category_label("observability") == "Observability"

    def test_title_cases_an_unknown_category(self) -> None:
        """So a scanner added tomorrow appears in a report before anyone
        writes it a label."""
        assert category_label("compliance") == "Compliance"


class TestPurity:
    def test_the_same_scan_assembles_identically_twice(self) -> None:
        """No clock anywhere. A document that stamped the current time would
        differ on every render, making a cached copy indistinguishable from a
        stale one — which is what milestone 5 depends on."""
        scan = make_scan()
        project = make_project()
        findings = [make_finding()]

        assert build_report(scan, project=project, findings=findings) == build_report(
            scan, project=project, findings=findings
        )
