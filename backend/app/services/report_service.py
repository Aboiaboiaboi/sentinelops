"""What a report says, decided in one place and without a renderer.

A pure function from a scan, its project and its findings to a `ReportData`
tree. No database access, no filesystem, no clock — everything the document
states is derived from what was passed in.

**Why this is separate from rendering at all.** The renderer for Phase 4 is
chosen by measurement rather than by preference, so it is the piece most likely
to be replaced; the content is not. Keeping "what the report says" here means a
renderer swap changes a template and none of the decisions below, and it means
those decisions can be tested with no PDF library installed anywhere.

**Why there is no `generated_at`.** A document that stamped the current time
would differ on every render, which makes a cached copy indistinguishable from
a stale one and quietly defeats milestone 5. The scan's own timestamps are what
a reader actually wants — when the code was scanned, not when the file was
made. Deterministic output is a property worth keeping.

Nothing here escapes or formats for markup. Every string is passed through as
it came out of the database, and escaping is the renderer's job, at its own
boundary, where it can be done once for the format actually being produced.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models import CategoryStatus, Finding, Project, Scan
from app.models.scan import SCAN_ERROR_HINTS
from app.scanners.base import CheckOutcome, Severity
from app.services.scoring_service import CATEGORY_WEIGHTS, category_max_score, score_to_grade

# Display names for the six categories, matching the frontend's CATEGORY_LABELS.
# A category with no entry is title-cased, so a scanner added tomorrow appears
# in a report before anyone writes it a label.
CATEGORY_LABELS: Mapping[str, str] = {
    "security": "Security",
    "reliability": "Reliability",
    "architecture": "Architecture",
    "deployment": "Deployment",
    "observability": "Observability",
    "scalability": "Scalability",
}

# Most severe first — the order a report is read in, and the order
# scan_service.list_findings already returns. Ranked explicitly rather than by
# the enum's declaration order: Severity is a StrEnum, so sorting it directly
# sorts alphabetically and puts CRITICAL after both HIGH and LOW.
_SEVERITY_RANK: Mapping[str, int] = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}

# Checks are listed worst-first for the same reason as findings: the failures
# are what the reader is looking for. `errored` outranks `skipped` because it is
# our failure to report on something, and burying it would make an incomplete
# scan look thorough.
_OUTCOME_RANK: Mapping[str, int] = {
    CheckOutcome.FAILED: 0,
    CheckOutcome.ERRORED: 1,
    CheckOutcome.SKIPPED: 2,
    CheckOutcome.PASSED: 3,
}

#: How much of a commit subject a report shows before truncating.
COMMIT_SUBJECT_LIMIT = 120


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category[:1].upper() + category[1:])


@dataclass(frozen=True, slots=True)
class ReportCategory:
    """One row of the category breakdown."""

    category: str
    label: str
    status: str
    #: Points earned, or None for a category that did not report. None rather
    #: than zero, which would read as "assessed and found terrible" — the same
    #: distinction scoring_service.score_by_category exists to preserve.
    score: int | None
    max_score: int

    @property
    def reported(self) -> bool:
        return self.status == CategoryStatus.COMPLETED


@dataclass(frozen=True, slots=True)
class ReportFinding:
    severity: str
    title: str
    description: str
    recommendation: str
    score_impact: int


@dataclass(frozen=True, slots=True)
class ReportCheck:
    id: str
    title: str
    outcome: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class ReportGroup[T]:
    """A category's worth of findings or checks, with its heading."""

    category: str
    label: str
    items: tuple[T, ...]


@dataclass(frozen=True, slots=True)
class ReportCommit:
    """The commit the scan looked at. Absent when the checkout had no HEAD."""

    sha: str
    #: The first seven characters, which is what a person recognises a commit by.
    short_sha: str
    #: The first line of the message, truncated. A report is a fixed-width
    #: document and a body paragraph in a header field wrecks the layout.
    subject: str
    author: str | None
    committed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReportData:
    """Everything a report states, and nothing about how it looks."""

    scan_id: uuid.UUID
    #: The user's label for the scan, or None — a renderer falls back to the
    #: timestamp, exactly as the UI does.
    scan_name: str | None
    project_name: str
    repository_url: str
    status: str

    score: int | None
    grade: str
    max_score: int
    scoring_version: str | None

    created_at: datetime
    completed_at: datetime | None

    categories: tuple[ReportCategory, ...]
    findings: tuple[ReportGroup[ReportFinding], ...]
    checks: tuple[ReportGroup[ReportCheck], ...]
    commit: ReportCommit | None

    #: Why a failed scan failed, and what to try. Null for anything that did not
    #: fail. The hint is derived from the category the same way ScanRead derives
    #: it, so a report and the screen give the same advice.
    error_category: str | None
    error_detail: str | None
    error_hint: str | None

    @property
    def reported_categories(self) -> int:
        return sum(1 for category in self.categories if category.reported)

    @property
    def total_categories(self) -> int:
        return len(self.categories)

    @property
    def complete(self) -> bool:
        """Whether every category reported.

        A report for a partial scan has to say so on its face. The score is a
        sum over the categories that reported, so 62 out of 100 with two
        categories missing is not the same claim as 62 with all six.
        """
        return self.total_categories > 0 and self.reported_categories == self.total_categories

    @property
    def finding_count(self) -> int:
        return sum(len(group.items) for group in self.findings)


def _category_sort_key(category: str) -> tuple[int, str]:
    """Heaviest category first, then alphabetically.

    Matches the frontend's chart ordering, so the PDF and the screen list the
    same six in the same sequence. An unrecognised category weighs zero and
    sorts to the end rather than being dropped.
    """
    return (-category_max_score(category), category)


def _build_categories(scan: Scan) -> tuple[ReportCategory, ...]:
    statuses: Mapping[str, Any] = scan.category_status or {}
    scores: Mapping[str, Any] = scan.category_scores or {}

    rows = []
    for category, status in statuses.items():
        if not status:
            continue
        maximum = category_max_score(category)
        completed = status == CategoryStatus.COMPLETED
        rows.append(
            ReportCategory(
                category=category,
                label=category_label(category),
                status=str(status),
                # Falling back to the cap for a completed category with no
                # points recorded, which is what scans predating
                # `category_scores` look like. Same fallback as the chart.
                score=scores.get(category, maximum) if completed else None,
                max_score=maximum,
            )
        )

    return tuple(sorted(rows, key=lambda row: _category_sort_key(row.category)))


def _group_by_category[T](
    items: Iterable[tuple[str, T]], sort_key: Any
) -> tuple[ReportGroup[T], ...]:
    """Bucket (category, item) pairs into groups, ordered like the breakdown."""
    buckets: dict[str, list[T]] = {}
    for category, item in items:
        buckets.setdefault(category, []).append(item)

    return tuple(
        ReportGroup(
            category=category,
            label=category_label(category),
            items=tuple(sorted(buckets[category], key=sort_key)),
        )
        for category in sorted(buckets, key=_category_sort_key)
    )


def _build_findings(findings: Sequence[Finding]) -> tuple[ReportGroup[ReportFinding], ...]:
    return _group_by_category(
        (
            (
                finding.category,
                ReportFinding(
                    severity=str(finding.severity),
                    title=finding.title,
                    description=finding.description,
                    recommendation=finding.recommendation,
                    score_impact=finding.score_impact,
                ),
            )
            for finding in findings
        ),
        # Worst first, then by what it cost — a critical that deducted 8 belongs
        # above a critical that deducted 2.
        lambda item: (_SEVERITY_RANK.get(item.severity, len(_SEVERITY_RANK)), -item.score_impact),
    )


def _build_checks(check_results: Sequence[Any]) -> tuple[ReportGroup[ReportCheck], ...]:
    """Group the stored check results.

    `check_results` is JSONB, so it is whatever was written to the column — a
    list of maps today, and anything at all for a row written by an older
    version. Entries that are not maps are skipped rather than raising: a report
    that omits a malformed check is worth more to its reader than an endpoint
    that 500s on one bad row.
    """
    pairs = []
    for entry in check_results or ():
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason")
        pairs.append(
            (
                str(entry.get("category", "")),
                ReportCheck(
                    id=str(entry.get("id", "")),
                    title=str(entry.get("title", "")),
                    outcome=str(entry.get("outcome", "")),
                    reason=str(reason) if reason else None,
                ),
            )
        )

    return _group_by_category(
        pairs,
        # Failures first, then by title so a re-run of the same scan lists them
        # identically — insertion order depends on which scanner finished first,
        # which is not a fact about the repository.
        lambda item: (_OUTCOME_RANK.get(item.outcome, len(_OUTCOME_RANK)), item.title),
    )


def _build_commit(scan: Scan) -> ReportCommit | None:
    """The commit context, or None when there is none.

    The four columns are nullable together — an empty repository has no HEAD —
    but `commit_sha` alone is what decides this: a scan that recorded a sha and
    failed to read the author still identifies which code was scanned, which is
    the whole reason the section exists.
    """
    if not scan.commit_sha:
        return None

    subject = (scan.commit_message or "").strip().splitlines()
    first_line = subject[0] if subject else ""
    if len(first_line) > COMMIT_SUBJECT_LIMIT:
        first_line = first_line[: COMMIT_SUBJECT_LIMIT - 1].rstrip() + "…"

    return ReportCommit(
        sha=scan.commit_sha,
        short_sha=scan.commit_sha[:7],
        subject=first_line,
        author=scan.commit_author or None,
        committed_at=scan.committed_at,
    )


def build_report(scan: Scan, *, project: Project, findings: Sequence[Finding]) -> ReportData:
    """Assemble everything a report states.

    `project` is passed rather than reached through `scan.project`: the
    relationship is `lazy="raise_on_sql"`, so touching it here would raise
    instead of quietly emitting a query — which is the behaviour that keeps
    this function pure and its tests free of a database.
    """
    return ReportData(
        scan_id=scan.id,
        scan_name=scan.name,
        project_name=project.name,
        repository_url=project.repository_url,
        status=str(scan.status),
        score=scan.score,
        grade=score_to_grade(scan.score),
        max_score=sum(CATEGORY_WEIGHTS.values()),
        scoring_version=scan.scoring_version,
        created_at=scan.created_at,
        completed_at=scan.completed_at,
        categories=_build_categories(scan),
        findings=_build_findings(findings),
        checks=_build_checks(scan.check_results),
        commit=_build_commit(scan),
        error_category=scan.error_category,
        error_detail=scan.error_detail,
        error_hint=SCAN_ERROR_HINTS.get(scan.error_category) if scan.error_category else None,
    )
