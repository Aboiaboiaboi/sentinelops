"""Turning report data into a PDF.

The renderer boundary. `build_report` decided what the document says; this
decides only how it looks, and it is the piece most likely to be replaced —
which is why it sits behind a Protocol and why nothing above it imports fpdf2.

**Why fpdf2, measured rather than assumed.** WeasyPrint produces the better
document from ~20 lines of CSS, and cannot import on Windows: it needs GTK,
which arrives as an out-of-band installer no lockfile records, so `uv sync`
would stop reproducing a working environment. The backend suite runs locally on
Windows, so that breaks the development loop rather than one test. Headless
Chromium was never in contention — a JavaScript engine rendering strings that
came out of a scanned repository is the same problem the sandbox exists for.
fpdf2 costs a hand-built layout and a vendored font; both are paid here, once.

**Why the font is vendored rather than installed.** fpdf2's built-in fonts are
latin-1 only, and repository text is arbitrary Unicode — the first spike render
died on an em dash in a finding description. Apt-installing DejaVu in the image
would fix the container and leave Windows broken, which is the trap that ruled
WeasyPrint out. The file ships in the repository so every environment renders
identically. See app/assets/fonts/LICENSE.txt.

**The security content of this module is `_clean`.** Every string reaching a
page originates in a scanned repository — file paths, package names, commit
subjects, finding descriptions built around them. fpdf2 does not execute
anything, so this is not injection in the WeasyPrint or Chromium sense. It is
three measured failure modes:

  * A codepoint the font lacks is dropped **silently**, with a warning to
    stderr and nothing on the page. `config 配置 file` renders as
    `config  file` — a report quietly missing part of a filename is worse than
    one that shows it could not draw it.
  * `cell()` swallows a newline and glues the words either side together, so a
    two-line commit subject becomes one run-on string.
  * Text length is unbounded — the `description` column is `Text`. 100k
    characters renders 20 pages. A report is a document, not a log.
"""

import functools
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.services.report_service import ReportCheck, ReportData, ReportGroup

if TYPE_CHECKING:
    from fpdf import FPDF

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
FONT_FAMILY = "DejaVu"

# Shown in place of a codepoint the font cannot draw. U+FFFD is present in
# DejaVu — checked, because falling back to a character the font also lacks
# would reintroduce the silent drop this exists to prevent.
REPLACEMENT = "�"

# Per-field ceilings. Titles are already bounded by their columns; descriptions
# and recommendations are `Text` and bounded only here.
TITLE_LIMIT = 200
BODY_LIMIT = 1500
FIELD_LIMIT = 300

# A4 in millimetres, with margins that leave a comfortable measure.
PAGE_MARGIN = 16.0
LINE = 5.0


def _load_supported_codepoints() -> frozenset[int]:
    """Every codepoint DejaVu can draw, read from the font's own cmap.

    fontTools arrives with fpdf2, so this costs no new dependency. Read from
    the regular weight only: DejaVu's bold covers the same set, and a mismatch
    would show as a missing glyph in a heading, which is visible.
    """
    from fontTools.ttLib import TTFont

    with TTFont(FONT_DIR / "DejaVuSans.ttf") as font:
        return frozenset(font.getBestCmap())


@functools.cache
def _supported() -> frozenset[int]:
    """Parsed once per process. ~5,900 codepoints; the parse is not free."""
    return _load_supported_codepoints()


def _clean(text: str | None, *, limit: int) -> str:
    """Make a repository-derived string safe to put on a page.

    Control characters become spaces rather than being stripped, so words
    either side of a newline stay separate words. Whitespace runs collapse for
    the same reason a report is not a log: the layout is fixed-width and the
    original spacing carries no meaning a reader can use.
    """
    if not text:
        return ""

    characters = []
    for character in text:
        # Cc is control, Cs is a lone surrogate, Cn is unassigned. None of them
        # can be drawn, and a lone surrogate additionally cannot be encoded.
        if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Cn"}:
            characters.append(" ")
        elif ord(character) in _supported():
            characters.append(character)
        else:
            characters.append(REPLACEMENT)

    collapsed = " ".join("".join(characters).split())
    if len(collapsed) <= limit:
        return collapsed
    # Truncated on a word boundary where there is one nearby, so the last line
    # does not end mid-token.
    cut = collapsed[: limit - 1]
    spaced = cut.rsplit(" ", 1)[0]
    return (spaced if len(spaced) > limit * 0.6 else cut).rstrip() + "…"


@runtime_checkable
class ReportRenderer(Protocol):
    """What the rest of the application is allowed to assume about rendering.

    Synchronous, for the same reason SandboxRunner is: this is CPU-bound work
    in a C-accelerated library, and the caller dispatches it with
    `asyncio.to_thread` rather than making the whole call chain async for a
    function that never awaits anything.
    """

    def render(self, report: ReportData) -> bytes:
        """Produce a complete PDF document for `report`."""
        ...


class PdfReportRenderer:
    """Renders with fpdf2 onto A4."""

    def render(self, report: ReportData) -> bytes:
        pdf = self._new_document(report)
        self._heading(pdf, report)
        self._score(pdf, report)
        if report.error_category:
            self._failure(pdf, report)
        self._categories(pdf, report)
        self._commit(pdf, report)
        self._findings(pdf, report)
        self._checks(pdf, report)
        return bytes(pdf.output())

    # -- document ---------------------------------------------------------

    def _new_document(self, report: ReportData) -> "FPDF":
        from fpdf import FPDF

        class Document(FPDF):
            def footer(self) -> None:
                # Identifies which scan a printed page came from, which is the
                # whole reason somebody keeps the file.
                self.set_y(-12)
                self.set_font(FONT_FAMILY, "", 7)
                self.set_text_color(120)
                self.cell(0, 4, f"SentinelOps · scan {report.scan_id}", align="L")
                self.cell(0, 4, f"Page {self.page_no()} of {{nb}}", align="R")
                self.set_text_color(0)

        pdf = Document(format="A4")
        # Stamped from the scan rather than from the clock. fpdf2 writes
        # /CreationDate as "now" by default, which would make two renders of an
        # unchanged scan differ by the second — undoing the determinism
        # build_report was written to preserve, and leaving milestone 5 unable
        # to tell a cached copy from a stale one by comparing bytes.
        pdf.set_creation_date(report.created_at)
        pdf.set_title("Production readiness report")
        pdf.set_creator("SentinelOps")
        # No set_author: the value would have to come from the project owner,
        # and a document that travels outside the app should not carry an
        # account holder's identity in metadata nobody thinks to look at.
        pdf.set_margins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_font(FONT_FAMILY, "", str(FONT_DIR / "DejaVuSans.ttf"))
        pdf.add_font(FONT_FAMILY, "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        # Resolves {nb} in the footer once the page count is known.
        pdf.alias_nb_pages()
        # No set_text_shaping(True). It needs uharfbuzz, a native extension —
        # and avoiding native dependencies is the entire reason fpdf2 was
        # chosen over WeasyPrint. Without shaping, a right-to-left run in a
        # commit subject renders in logical rather than visual order: wrong,
        # but present and legible character by character, where the dependency
        # would put the Windows development loop back at risk of the failure
        # this renderer exists to avoid.
        pdf.add_page()
        return pdf

    # -- primitives -------------------------------------------------------
    #
    # Every write goes through one of these. `new_x="LMARGIN"` on each is not
    # decoration: fpdf2 leaves the cursor at the *right* margin after a
    # multi_cell, so the next full-width write gets zero width and raises
    # "Not enough horizontal space to render a single character" — a runtime
    # error triggered by the length of a string from a scanned repository.

    def _line(
        self,
        pdf: "FPDF",
        text: str,
        *,
        size: float = 9.5,
        bold: bool = False,
        grey: bool = False,
        height: float = LINE,
    ) -> None:
        pdf.set_font(FONT_FAMILY, "B" if bold else "", size)
        pdf.set_text_color(110 if grey else 24)
        pdf.multi_cell(0, height, text, new_x="LMARGIN")
        pdf.set_text_color(0)

    def _section(self, pdf: "FPDF", title: str) -> None:
        pdf.ln(4)
        pdf.set_font(FONT_FAMILY, "B", 11)
        pdf.set_text_color(24)
        pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        # A rule under the heading, which is the one piece of chrome that makes
        # a dense single-column document scannable.
        pdf.set_draw_color(210)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)

    def _row(self, pdf: "FPDF", left: str, right: str) -> None:
        pdf.set_font(FONT_FAMILY, "", 9.5)
        width = pdf.w - pdf.l_margin - pdf.r_margin
        pdf.cell(width * 0.7, 5.5, left)
        pdf.cell(width * 0.3, 5.5, right, align="R", new_x="LMARGIN", new_y="NEXT")

    # -- sections ---------------------------------------------------------

    def _heading(self, pdf: "FPDF", report: ReportData) -> None:
        pdf.set_font(FONT_FAMILY, "B", 19)
        pdf.cell(0, 9, "Production readiness report", new_x="LMARGIN", new_y="NEXT")

        self._line(
            pdf,
            f"{_clean(report.project_name, limit=FIELD_LIMIT)} · "
            f"{_clean(report.repository_url, limit=FIELD_LIMIT)}",
            grey=True,
            height=4.5,
        )
        # The scan's own name when it has one, and the timestamp regardless —
        # a named scan is still identified by when it ran.
        if report.scan_name:
            self._line(pdf, _clean(report.scan_name, limit=FIELD_LIMIT), grey=True, height=4.5)

        scanned = f"Scanned {report.created_at:%d %B %Y, %H:%M} UTC"
        if report.scoring_version:
            scanned += f" · scoring {_clean(report.scoring_version, limit=20)}"
        self._line(pdf, scanned, grey=True, height=4.5)

    def _score(self, pdf: "FPDF", report: ReportData) -> None:
        pdf.ln(3)
        pdf.set_font(FONT_FAMILY, "B", 30)
        headline = "—" if report.score is None else f"{report.score}/{report.max_score}"
        pdf.cell(0, 13, f"{headline}    Grade {report.grade}", new_x="LMARGIN", new_y="NEXT")

        self._line(
            pdf,
            f"{report.reported_categories} of {report.total_categories} categories reported",
            grey=True,
            height=4.5,
        )
        if not report.complete:
            # Said on the face of the document. The score is a sum over the
            # categories that reported, so 62 with two missing is not the same
            # claim as 62 with all six, and a reader cannot infer that from a
            # number alone.
            self._line(
                pdf,
                "This score covers only the categories that reported. Categories that did "
                "not report contribute nothing rather than a reduced total.",
                size=8.5,
                grey=True,
                height=4,
            )

    def _failure(self, pdf: "FPDF", report: ReportData) -> None:
        self._section(pdf, "Why this scan did not finish")
        if report.error_detail:
            self._line(pdf, _clean(report.error_detail, limit=BODY_LIMIT))
        if report.error_hint:
            self._line(pdf, _clean(report.error_hint, limit=BODY_LIMIT), grey=True)

    def _categories(self, pdf: "FPDF", report: ReportData) -> None:
        self._section(pdf, "Category breakdown")
        if not report.categories:
            self._line(pdf, "No categories were assessed.", grey=True)
            return
        for row in report.categories:
            points = (
                f"{row.score}/{row.max_score}"
                if row.score is not None
                else f"not assessed · {row.status}"
            )
            self._row(pdf, _clean(row.label, limit=TITLE_LIMIT), points)

    def _commit(self, pdf: "FPDF", report: ReportData) -> None:
        if report.commit is None:
            return
        self._section(pdf, "Commit scanned")
        self._line(
            pdf,
            f"{report.commit.short_sha}  {_clean(report.commit.subject, limit=TITLE_LIMIT)}",
            bold=True,
        )
        byline = []
        if report.commit.author:
            byline.append(_clean(report.commit.author, limit=FIELD_LIMIT))
        if report.commit.committed_at:
            byline.append(f"{report.commit.committed_at:%d %B %Y, %H:%M} UTC")
        if byline:
            self._line(pdf, " · ".join(byline), grey=True, height=4.5)

    def _findings(self, pdf: "FPDF", report: ReportData) -> None:
        self._section(pdf, f"Findings ({report.finding_count})")
        if not report.findings:
            self._line(pdf, "No findings were recorded.", grey=True)
            return

        for group in report.findings:
            self._line(pdf, _clean(group.label, limit=TITLE_LIMIT), size=10, bold=True)
            for item in group.items:
                self._line(
                    pdf,
                    f"[{_clean(item.severity, limit=20)}] "
                    f"{_clean(item.title, limit=TITLE_LIMIT)}  (−{item.score_impact})",
                    size=9,
                    bold=True,
                    height=4.5,
                )
                self._line(pdf, _clean(item.description, limit=BODY_LIMIT), size=9, height=4.5)
                self._line(
                    pdf,
                    f"Fix: {_clean(item.recommendation, limit=BODY_LIMIT)}",
                    size=9,
                    grey=True,
                    height=4.5,
                )
                pdf.ln(1.5)

    def _checks(self, pdf: "FPDF", report: ReportData) -> None:
        self._section(pdf, "Checks performed")
        if not report.checks:
            self._line(pdf, "No checks were recorded.", grey=True)
            return

        for group in report.checks:
            self._line(pdf, _clean(group.label, limit=TITLE_LIMIT), size=10, bold=True)
            self._check_rows(pdf, group)

    def _check_rows(self, pdf: "FPDF", group: ReportGroup[ReportCheck]) -> None:
        width = pdf.w - pdf.l_margin - pdf.r_margin
        for item in group.items:
            pdf.set_font(FONT_FAMILY, "", 8.5)
            pdf.set_text_color(110)
            pdf.cell(20, 4.5, _clean(item.outcome, limit=20))
            pdf.set_text_color(24)
            # The title takes the rest of the measure, wrapped — a check title
            # is written for a person and can be long.
            pdf.multi_cell(width - 20, 4.5, _clean(item.title, limit=TITLE_LIMIT), new_x="LMARGIN")
            pdf.set_text_color(0)
            if item.reason:
                pdf.set_font(FONT_FAMILY, "", 8)
                pdf.set_text_color(140)
                pdf.set_x(pdf.l_margin + 20)
                pdf.multi_cell(
                    width - 20, 4, _clean(item.reason, limit=BODY_LIMIT), new_x="LMARGIN"
                )
                pdf.set_text_color(0)


# The real renderer is the default, unlike the queue, sandbox and storage
# boundaries, which each default to something that refuses.
#
# Those three refuse because each depends on something outside the process that
# may genuinely be absent — a broker, a container runtime, a bucket — and
# guessing wrong produces a silently unrun job, a falsely passing check, or an
# unsaved report. fpdf2 is a pinned dependency of this application: it is either
# installed or the process does not import. There is no misconfiguration for a
# refusing default to catch, and one would only mean every report in the test
# suite failed for no safety gained. The accessors exist so milestone 5 can
# count renders, and so a test can substitute a cheap stand-in.
_renderer: ReportRenderer = PdfReportRenderer()


def get_report_renderer() -> ReportRenderer:
    return _renderer


def set_report_renderer(renderer: ReportRenderer) -> None:
    """Swap the implementation. Tests that assert on caching count calls
    through a stand-in installed here."""
    global _renderer
    _renderer = renderer
