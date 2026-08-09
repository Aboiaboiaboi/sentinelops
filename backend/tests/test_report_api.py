"""Tests for GET /scans/{id}/report.

The negative cases carry the weight here. This route returns a file rather than
JSON, so a mistake in it is a mistake in what leaves the building: somebody
else's findings, a header a scan name injected, or a document for a scan that
has not looked at anything yet.
"""

import io
import re
import uuid

import pytest
from httpx import AsyncClient
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reports import download_filename
from app.config import get_settings
from app.models import Finding, Scan, ScanStatus, Severity
from app.services.report_service import build_report
from tests.test_report_data import make_project, make_scan

PROJECT = {"name": "Checkout service", "repository_url": "https://github.com/acme/checkout"}


@pytest.fixture
async def project_id(authed_client: AsyncClient) -> str:
    return (await authed_client.post("/projects", json=PROJECT)).json()["id"]


async def finish_scan(
    session: AsyncSession,
    scan_id: str,
    *,
    status: ScanStatus = ScanStatus.COMPLETED,
    score: int | None = 63,
    with_finding: bool = False,
) -> None:
    """Put a scan into a terminal state directly.

    Faster and more controllable than running a worker, and this module is
    testing the route rather than the lifecycle.
    """
    scan = await session.get(Scan, uuid.UUID(scan_id))
    scan.status = status
    scan.score = score
    scan.scoring_version = "v2"
    scan.category_status = {"security": "completed", "reliability": "completed"}
    scan.category_scores = {"security": 20, "reliability": 18}
    scan.check_results = [
        {"id": "sec-1", "category": "security", "title": "Debug mode off", "outcome": "passed"}
    ]
    if with_finding:
        session.add(
            Finding(
                scan_id=scan.id,
                category="security",
                severity=Severity.HIGH,
                title="Hardcoded credential",
                description="A token is committed in config.py.",
                recommendation="Move it to an environment variable.",
                score_impact=5,
            )
        )
    await session.flush()


@pytest.fixture
async def finished_scan_id(
    authed_client: AsyncClient, project_id: str, session: AsyncSession
) -> str:
    scan_id = (await authed_client.post(f"/projects/{project_id}/scans")).json()["id"]
    await finish_scan(session, scan_id, with_finding=True)
    return scan_id


class TestServingTheReport:
    async def test_returns_a_pdf(self, authed_client: AsyncClient, finished_scan_id: str) -> None:
        response = await authed_client.get(f"/scans/{finished_scan_id}/report")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")

    async def test_the_document_says_what_the_scan_found(
        self, authed_client: AsyncClient, finished_scan_id: str
    ) -> None:
        """Asserted on extracted text: a valid empty PDF is still a valid PDF."""
        response = await authed_client.get(f"/scans/{finished_scan_id}/report")

        reader = PdfReader(io.BytesIO(response.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "Production readiness report" in text
        assert "Checkout service" in text
        assert "63/100" in text
        assert "Hardcoded credential" in text

    async def test_offers_it_as_a_download(
        self, authed_client: AsyncClient, finished_scan_id: str
    ) -> None:
        response = await authed_client.get(f"/scans/{finished_scan_id}/report")

        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert ".pdf" in disposition

    async def test_is_not_cached(self, authed_client: AsyncClient, finished_scan_id: str) -> None:
        """A report lists this repository's security findings."""
        response = await authed_client.get(f"/scans/{finished_scan_id}/report")

        assert response.headers["cache-control"] == "no-store"

    async def test_a_failed_scan_still_gets_a_report(
        self, authed_client: AsyncClient, project_id: str, session: AsyncSession
    ) -> None:
        """It carries the failure and the hint, which is the document somebody
        actually wants in that case."""
        scan_id = (await authed_client.post(f"/projects/{project_id}/scans")).json()["id"]
        await finish_scan(session, scan_id, status=ScanStatus.FAILED, score=None)
        scan = await session.get(Scan, uuid.UUID(scan_id))
        scan.error_category = "repository_not_found"
        scan.error_detail = "The repository could not be reached."
        await session.flush()

        response = await authed_client.get(f"/scans/{scan_id}/report")

        assert response.status_code == 200
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(response.content)).pages
        )
        assert "Why this scan did not finish" in text


class TestRefusals:
    @pytest.mark.parametrize("state", [ScanStatus.PENDING, ScanStatus.RUNNING])
    async def test_an_unfinished_scan_is_a_conflict_not_a_404(
        self,
        authed_client: AsyncClient,
        project_id: str,
        session: AsyncSession,
        state: ScanStatus,
    ) -> None:
        """The scan exists and this is its report; it does not exist *yet*.
        Those are different facts and a 404 would state the wrong one."""
        scan_id = (await authed_client.post(f"/projects/{project_id}/scans")).json()["id"]
        scan = await session.get(Scan, uuid.UUID(scan_id))
        scan.status = state
        await session.flush()

        response = await authed_client.get(f"/scans/{scan_id}/report")

        assert response.status_code == 409
        assert "has not finished" in response.json()["detail"]

    async def test_a_scan_that_does_not_exist_is_a_404(self, authed_client: AsyncClient) -> None:
        response = await authed_client.get(f"/scans/{uuid.uuid4()}/report")

        assert response.status_code == 404

    async def test_another_users_scan_is_a_404(
        self, authed_client: AsyncClient, other_client: AsyncClient, finished_scan_id: str
    ) -> None:
        response = await other_client.get(f"/scans/{finished_scan_id}/report")

        assert response.status_code == 404

    async def test_another_users_scan_answers_exactly_like_a_missing_one(
        self, authed_client: AsyncClient, other_client: AsyncClient, finished_scan_id: str
    ) -> None:
        """Otherwise the endpoint is an oracle for which scan ids exist."""
        theirs = await other_client.get(f"/scans/{finished_scan_id}/report")
        absent = await other_client.get(f"/scans/{uuid.uuid4()}/report")

        assert theirs.status_code == absent.status_code
        assert theirs.json() == absent.json()

    async def test_an_unfinished_scan_of_another_user_does_not_leak_its_state(
        self,
        authed_client: AsyncClient,
        other_client: AsyncClient,
        project_id: str,
    ) -> None:
        """409 is only ever an answer about your own scan. Reaching the status
        check before the ownership check would make it a membership test."""
        scan_id = (await authed_client.post(f"/projects/{project_id}/scans")).json()["id"]

        response = await other_client.get(f"/scans/{scan_id}/report")

        assert response.status_code == 404

    async def test_signed_out_is_refused(self, client: AsyncClient, finished_scan_id: str) -> None:
        client.cookies.clear()

        response = await client.get(f"/scans/{finished_scan_id}/report")

        assert response.status_code == 401


class TestRateLimit:
    async def test_the_limit_fires(self, authed_client: AsyncClient, finished_scan_id: str) -> None:
        """The most expensive GET in the API, reachable by opening a tab in a
        loop. Everything else outside auth is deliberately unlimited."""
        allowed = int(get_settings().report_rate_limit.split("/")[0])

        for _ in range(allowed):
            assert (await authed_client.get(f"/scans/{finished_scan_id}/report")).status_code == 200

        response = await authed_client.get(f"/scans/{finished_scan_id}/report")

        assert response.status_code == 429
        assert "detail" in response.json()


class TestTheFilename:
    """Scans and projects are user-named, and this value lands in a response
    header and then on a filesystem."""

    def _name(self, **overrides: object) -> str:
        scan = make_scan(**overrides)
        return download_filename(build_report(scan, project=make_project(), findings=[]))

    def test_uses_the_scan_name_when_there_is_one(self) -> None:
        assert self._name(name="Before the refactor").startswith("sentinelops-before-the-refactor-")

    def test_falls_back_to_the_project_name(self) -> None:
        assert self._name().startswith("sentinelops-checkout-service-")

    def test_ends_with_the_scan_date(self) -> None:
        assert self._name().endswith("-2026-08-01.pdf")

    @pytest.mark.parametrize(
        "hostile",
        [
            'evil"; rm -rf /',
            "../../etc/passwd",
            "name\r\nX-Injected: yes",
            "NUL",
            "a/b\\c:d*e?f",
            "\x00\x01\x02",
        ],
    )
    def test_reduces_hostile_names_to_safe_characters(self, hostile: str) -> None:
        filename = self._name(name=hostile)

        assert re.fullmatch(r"[a-z0-9.\-]+", filename), filename
        for character in '"\\/;:\r\n\x00*?':
            assert character not in filename

    def test_a_name_of_only_punctuation_still_yields_a_filename(self) -> None:
        """Reducing to empty must not produce `sentinelops--2026-08-01.pdf` or
        a file with no stem at all."""
        assert self._name(name="!!!***") == "sentinelops-2026-08-01.pdf"

    def test_a_non_latin_name_still_yields_a_filename(self) -> None:
        assert self._name(name="配置レポート") == "sentinelops-2026-08-01.pdf"

    def test_a_very_long_name_is_truncated(self) -> None:
        filename = self._name(name="word " * 200)

        assert len(filename) < 100

    async def test_the_header_survives_a_hostile_name_end_to_end(
        self, authed_client: AsyncClient, finished_scan_id: str
    ) -> None:
        """The real test of the sanitiser: a CRLF in a scan name must not put a
        second header on the response."""
        await authed_client.patch(
            f"/scans/{finished_scan_id}", json={"name": 'x"\r\nX-Injected: yes'}
        )

        response = await authed_client.get(f"/scans/{finished_scan_id}/report")

        assert response.status_code == 200
        assert "x-injected" not in response.headers
        assert "\r" not in response.headers["content-disposition"]
