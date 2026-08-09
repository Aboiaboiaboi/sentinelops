"""Tests for the PDF renderer.

Assertions are on **text extracted back out of the document**, not on byte
length. A valid empty PDF is a valid PDF, and "it produced bytes" is this
phase's version of trusting a tool's empty output — the mistake Phase 3 was
built to stop making.

The hostile-string cases are the security content. Every string on a page came
out of a scanned repository, and the spike measured three ways that goes wrong:
a codepoint the font lacks is dropped silently, `cell()` glues words across a
newline, and text length is unbounded.
"""

import io
import re
from datetime import UTC, datetime

import pytest
from pypdf import PdfReader

from app.models import CategoryStatus, ScanStatus, Severity
from app.services.report_renderer import (
    BODY_LIMIT,
    REPLACEMENT,
    PdfReportRenderer,
    ReportRenderer,
    _clean,
    get_report_renderer,
    set_report_renderer,
)
from app.services.report_service import build_report
from tests.test_report_data import make_finding, make_project, make_scan


def render(scan_overrides: dict[str, object] | None = None, findings: list[object] | None = None):
    scan = make_scan(**(scan_overrides or {}))
    report = build_report(scan, project=make_project(), findings=findings or [])
    return PdfReportRenderer().render(report)


def text_of(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def pages_of(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


class TestTheDocument:
    def test_produces_a_readable_pdf(self) -> None:
        pdf = render()

        assert pdf.startswith(b"%PDF-")
        assert "Production readiness report" in text_of(pdf)

    def test_states_the_score_and_grade(self) -> None:
        pdf = render({"score": 63})

        text = text_of(pdf)
        assert "63/100" in text
        assert "Grade D" in text

    def test_names_the_project_and_repository(self) -> None:
        text = text_of(render())

        assert "Checkout service" in text
        assert "github.com/acme/checkout" in text

    def test_lists_every_category_with_its_points(self) -> None:
        text = text_of(render())

        for label in ("Security", "Reliability", "Architecture", "Deployment", "Scalability"):
            assert label in text
        assert "25/25" in text

    def test_shows_the_scan_name_when_there_is_one(self) -> None:
        text = text_of(render({"name": "Before the refactor"}))

        assert "Before the refactor" in text

    def test_numbers_the_pages_and_identifies_the_scan(self) -> None:
        """A printed page has to say which scan it came from — that is the
        whole reason somebody keeps the file."""
        scan = make_scan()
        report = build_report(scan, project=make_project(), findings=[])
        text = text_of(PdfReportRenderer().render(report))

        assert "Page 1 of 1" in text
        assert str(scan.id) in text


class TestFindings:
    def test_renders_a_finding_in_full(self) -> None:
        finding = make_finding(
            title="Hardcoded credential",
            description="A token is committed in config.py.",
            recommendation="Move it to an environment variable.",
        )

        text = text_of(render(findings=[finding]))

        assert "Hardcoded credential" in text
        assert "A token is committed in config.py." in text
        assert "Move it to an environment variable." in text

    def test_says_so_when_there_are_none(self) -> None:
        """Rather than an empty heading, which reads as a rendering failure."""
        assert "No findings were recorded." in text_of(render())

    def test_groups_findings_under_their_category(self) -> None:
        findings = [
            make_finding(category="security", title="A security problem"),
            make_finding(category="scalability", title="A scalability problem"),
        ]

        text = text_of(render(findings=findings))

        assert text.index("A security problem") < text.index("A scalability problem")

    def test_shows_the_severity_and_the_deduction(self) -> None:
        finding = make_finding(severity=Severity.CRITICAL, score_impact=8)

        text = text_of(render(findings=[finding]))

        assert "CRITICAL" in text
        assert "8" in text


class TestChecks:
    def test_lists_checks_with_their_outcome(self) -> None:
        scan = {
            "check_results": [
                {
                    "id": "sec-1",
                    "category": "security",
                    "title": "Debug mode off",
                    "outcome": "passed",
                },
                {
                    "id": "sec-2",
                    "category": "security",
                    "title": "No secrets in source",
                    "outcome": "failed",
                },
            ]
        }

        text = text_of(render(scan))

        assert "Checks performed" in text
        assert "Debug mode off" in text
        assert "No secrets in source" in text
        assert "failed" in text

    def test_shows_why_a_check_was_skipped(self) -> None:
        """A skip with no explanation is the dead end check results replaced."""
        scan = {
            "check_results": [
                {
                    "id": "sec-1",
                    "category": "security",
                    "title": "Env files protected",
                    "outcome": "skipped",
                    "reason": "the project does not load configuration from env files",
                }
            ]
        }

        text = text_of(render(scan))

        assert "the project does not load configuration from env files" in text

    def test_says_so_when_there_are_none(self) -> None:
        assert "No checks were recorded." in text_of(render())


class TestIncompleteScans:
    def test_a_partial_scan_says_so_on_its_face(self) -> None:
        """62 with two categories missing is not the same claim as 62 with all
        six, and a reader cannot infer that from a number."""
        text = text_of(
            render(
                {
                    "category_status": {
                        "security": CategoryStatus.COMPLETED.value,
                        "reliability": CategoryStatus.FAILED.value,
                    },
                    "category_scores": {"security": 20},
                    "score": 20,
                }
            )
        )

        assert "1 of 2 categories reported" in text
        assert "only the categories that reported" in text

    def test_a_category_that_did_not_report_is_marked_not_assessed(self) -> None:
        text = text_of(
            render(
                {
                    "category_status": {"security": CategoryStatus.FAILED.value},
                    "category_scores": {},
                    "score": 0,
                }
            )
        )

        assert "not assessed" in text

    def test_a_failed_scan_explains_itself(self) -> None:
        text = text_of(
            render(
                {
                    "status": ScanStatus.FAILED,
                    "score": None,
                    "error_category": "repository_not_found",
                    "error_detail": "The repository could not be reached.",
                }
            )
        )

        assert "Why this scan did not finish" in text
        assert "The repository could not be reached." in text
        assert "private" in text  # the hint

    def test_a_scan_with_no_score_shows_no_grade_rather_than_an_f(self) -> None:
        text = text_of(render({"score": None}))

        assert "Grade —" in text
        assert "Grade F" not in text


class TestCommitContext:
    def test_renders_the_commit(self) -> None:
        text = text_of(
            render(
                {
                    "commit_sha": "0123456789abcdef0123456789abcdef01234567",
                    "commit_message": "Add the health endpoint",
                    "commit_author": "Sebastián Ramírez",
                    "committed_at": datetime(2026, 7, 30, 9, 0, tzinfo=UTC),
                }
            )
        )

        assert "0123456" in text
        assert "Add the health endpoint" in text
        # Non-ASCII that DejaVu covers must survive intact, not be replaced.
        assert "Sebastián Ramírez" in text

    def test_the_section_is_absent_when_there_is_no_head(self) -> None:
        assert "Commit scanned" not in text_of(render())


class TestHostileStrings:
    """Every string on a page came out of a scanned repository."""

    def test_a_glyph_the_font_lacks_is_visible_rather_than_dropped(self) -> None:
        """Measured in the spike: fpdf2 drops an unsupported codepoint with
        only a warning, so `config 配置 file` renders as `config  file`. A
        report quietly missing part of a filename is worse than one that shows
        it could not draw it."""
        finding = make_finding(description="config 配置 file")

        text = text_of(render(findings=[finding]))

        assert REPLACEMENT in text
        assert "config" in text and "file" in text

    def test_a_newline_does_not_glue_words_together(self) -> None:
        """cell() swallows a newline, turning two lines into one run-on word."""
        assert _clean("line one\nline two", limit=200) == "line one line two"

    @pytest.mark.parametrize("control", ["\x00", "\x1b", "\r", "\t", "\x07"])
    def test_control_characters_become_spaces(self, control: str) -> None:
        assert _clean(f"a{control}b", limit=200) == "a b"

    def test_a_lone_surrogate_does_not_crash_the_render(self) -> None:
        """It cannot be encoded at all, so it must never reach fpdf2."""
        finding = make_finding(title="broken \ud800 title")

        pdf = render(findings=[finding])

        assert pdf.startswith(b"%PDF-")

    def test_unbounded_text_does_not_produce_an_unbounded_document(self) -> None:
        """`description` is a Text column. 100k characters rendered 20 pages in
        the spike; a report is a document, not a log."""
        finding = make_finding(description="word " * 20_000)

        assert pages_of(render(findings=[finding])) <= 3

    def test_truncation_is_marked(self) -> None:
        cleaned = _clean("word " * 20_000, limit=BODY_LIMIT)

        assert len(cleaned) <= BODY_LIMIT
        assert cleaned.endswith("…")

    def test_a_very_long_unbroken_token_still_renders(self) -> None:
        """A path with no spaces cannot be wrapped on a word boundary, and a
        zero-width cell raises rather than overflowing."""
        finding = make_finding(title="a" * 500, description="b" * 500)

        pdf = render(findings=[finding])

        assert pdf.startswith(b"%PDF-")

    def test_short_text_is_left_alone(self) -> None:
        assert _clean("Plain title", limit=200) == "Plain title"

    def test_none_and_empty_are_empty(self) -> None:
        assert _clean(None, limit=200) == ""
        assert _clean("   ", limit=200) == ""

    def test_markup_is_not_interpreted_and_not_lost(self) -> None:
        """fpdf2 draws text rather than parsing it, so the escaping question is
        whether the characters survive — not whether a tag executes."""
        finding = make_finding(title="<script>alert(1)</script> & co")

        text = text_of(render(findings=[finding]))

        assert "<script>" in text
        assert "& co" in text


class TestDeterminism:
    def test_two_renders_of_the_same_scan_are_byte_identical(self) -> None:
        """fpdf2 stamps /CreationDate from the clock by default, which would
        make an unchanged scan render differently one second later — and leave
        milestone 5 unable to tell a cached copy from a stale one."""
        report = build_report(make_scan(), project=make_project(), findings=[make_finding()])
        renderer = PdfReportRenderer()

        assert renderer.render(report) == renderer.render(report)

    def test_the_creation_date_comes_from_the_scan(self) -> None:
        pdf = render()

        assert re.search(rb"/CreationDate \(D:20260801", pdf)

    def test_no_author_metadata(self) -> None:
        """A document that travels outside the app should not carry the
        account holder's identity in metadata nobody thinks to look at."""
        reader = PdfReader(io.BytesIO(render()))

        assert not (reader.metadata or {}).get("/Author")


class TestTheBoundary:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(PdfReportRenderer(), ReportRenderer)

    def test_the_real_renderer_is_the_default(self) -> None:
        """Unlike the queue, sandbox and storage boundaries, which refuse.
        Those depend on something outside the process that may be absent;
        fpdf2 is either installed or this module does not import."""
        assert isinstance(get_report_renderer(), PdfReportRenderer)

    def test_the_renderer_is_swappable(self) -> None:
        class Stub:
            def render(self, report: object) -> bytes:
                return b"%PDF-stub"

        original = get_report_renderer()
        replacement = Stub()
        try:
            set_report_renderer(replacement)
            assert get_report_renderer() is replacement
        finally:
            set_report_renderer(original)
