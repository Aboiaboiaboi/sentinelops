"""Tests for caching the rendered report.

Cache hits are asserted by **counting renderer calls**, never by timing. A
timing assertion on a 100ms render is a flaky test that fails on a loaded CI
runner and passes on a quiet one, and it would not distinguish a cache hit from
a machine that happened to be fast.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Scan
from app.services import report_cache
from app.services.report_renderer import (
    PdfReportRenderer,
    ReportData,
    get_report_renderer,
    set_report_renderer,
)
from app.services.report_service import build_report
from app.utils.storage import LocalStorage, StorageUnavailable, get_storage, set_storage
from tests.test_report_api import PROJECT, finish_scan  # noqa: F401 — fixtures below use it
from tests.test_report_data import make_finding, make_project, make_scan


class CountingRenderer:
    """The real renderer, plus a tally. Real bytes so the response is real."""

    def __init__(self) -> None:
        self.calls = 0
        self._inner = PdfReportRenderer()

    def render(self, report: ReportData) -> bytes:
        self.calls += 1
        return self._inner.render(report)


@pytest.fixture
def renderer() -> Iterator[CountingRenderer]:
    previous = get_report_renderer()
    counting = CountingRenderer()
    set_report_renderer(counting)
    try:
        yield counting
    finally:
        set_report_renderer(previous)


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalStorage]:
    """Real storage for the duration of a test.

    The suite's default is NullStorage — lifespan never runs under
    ASGITransport — so a test that wants a cache has to install one, which is
    the boundary working as intended.
    """
    previous = get_storage()
    local = LocalStorage(tmp_path)
    set_storage(local)
    try:
        yield local
    finally:
        set_storage(previous)


@pytest.fixture
async def project_id(authed_client: AsyncClient) -> str:
    return (await authed_client.post("/projects", json=PROJECT)).json()["id"]


@pytest.fixture
async def scan_id(authed_client: AsyncClient, project_id: str, session: AsyncSession) -> str:
    new_id = (await authed_client.post(f"/projects/{project_id}/scans")).json()["id"]
    await finish_scan(session, new_id, with_finding=True)
    return new_id


class TestTheFingerprint:
    def test_the_same_report_fingerprints_the_same(self) -> None:
        report = build_report(make_scan(), project=make_project(), findings=[make_finding()])

        assert report_cache.fingerprint(report) == report_cache.fingerprint(report)

    def test_a_rename_changes_it(self) -> None:
        """Scans are renameable and renaming is history-preserving, so this is
        the invalidation that actually happens in normal use."""
        before = build_report(make_scan(), project=make_project(), findings=[])
        after = build_report(make_scan(name="After the fix"), project=make_project(), findings=[])

        assert report_cache.fingerprint(before) != report_cache.fingerprint(after)

    def test_a_renamed_project_changes_it(self) -> None:
        """The project name is on the document, and projects are editable."""
        before = build_report(make_scan(), project=make_project(), findings=[])
        after = build_report(make_scan(), project=make_project(name="Payments"), findings=[])

        assert report_cache.fingerprint(before) != report_cache.fingerprint(after)

    def test_a_different_finding_changes_it(self) -> None:
        """Covered because the whole tree is hashed, not a list of columns
        somebody has to remember to extend."""
        before = build_report(make_scan(), project=make_project(), findings=[make_finding()])
        after = build_report(
            make_scan(), project=make_project(), findings=[make_finding(title="Something else")]
        )

        assert report_cache.fingerprint(before) != report_cache.fingerprint(after)

    def test_the_render_version_changes_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A layout change is invisible to a hash of the data, so it gets its
        own lever."""
        report = build_report(make_scan(), project=make_project(), findings=[])
        before = report_cache.fingerprint(report)

        monkeypatch.setattr(report_cache, "RENDER_VERSION", "999")

        assert report_cache.fingerprint(report) != before

    def test_the_key_is_confined_to_the_scan(self) -> None:
        identifier = uuid.uuid4()

        key = report_cache.storage_key(identifier, "abcdef0123456789")

        assert key == f"scans/{identifier}/report-abcdef0123456789.pdf"


class TestCaching:
    async def test_two_requests_render_once(
        self,
        authed_client: AsyncClient,
        scan_id: str,
        renderer: CountingRenderer,
        storage: LocalStorage,
    ) -> None:
        first = await authed_client.get(f"/scans/{scan_id}/report")
        second = await authed_client.get(f"/scans/{scan_id}/report")

        assert first.status_code == second.status_code == 200
        assert renderer.calls == 1

    async def test_the_cached_copy_is_the_same_document(
        self,
        authed_client: AsyncClient,
        scan_id: str,
        renderer: CountingRenderer,
        storage: LocalStorage,
    ) -> None:
        first = await authed_client.get(f"/scans/{scan_id}/report")
        second = await authed_client.get(f"/scans/{scan_id}/report")

        assert first.content == second.content

    async def test_the_key_is_recorded_on_the_scan(
        self,
        authed_client: AsyncClient,
        scan_id: str,
        session: AsyncSession,
        renderer: CountingRenderer,
        storage: LocalStorage,
    ) -> None:
        await authed_client.get(f"/scans/{scan_id}/report")

        session.expire_all()
        scan = await session.get(Scan, uuid.UUID(scan_id))
        assert scan.report_key is not None
        assert scan.report_key.startswith(f"scans/{scan_id}/report-")

    async def test_renaming_the_scan_forces_a_fresh_render(
        self,
        authed_client: AsyncClient,
        scan_id: str,
        renderer: CountingRenderer,
        storage: LocalStorage,
    ) -> None:
        """A report downloaded on Tuesday and one downloaded on Friday should
        not disagree about the score — but both should agree with the app."""
        await authed_client.get(f"/scans/{scan_id}/report")
        await authed_client.patch(f"/scans/{scan_id}", json={"name": "After the fix"})

        response = await authed_client.get(f"/scans/{scan_id}/report")

        assert renderer.calls == 2
        assert "After the fix" in _text(response.content)

    async def test_a_rename_and_back_reuses_the_first_copy(
        self,
        authed_client: AsyncClient,
        scan_id: str,
        renderer: CountingRenderer,
        storage: LocalStorage,
    ) -> None:
        """The fingerprint describes the document, not a version counter, so
        returning to a previous state returns to its stored copy."""
        await authed_client.get(f"/scans/{scan_id}/report")
        await authed_client.patch(f"/scans/{scan_id}", json={"name": "Temporary"})
        await authed_client.get(f"/scans/{scan_id}/report")
        await authed_client.patch(f"/scans/{scan_id}", json={"name": None})

        await authed_client.get(f"/scans/{scan_id}/report")

        assert renderer.calls == 2

    async def test_a_missing_stored_object_is_rendered_again(
        self,
        authed_client: AsyncClient,
        scan_id: str,
        session: AsyncSession,
        renderer: CountingRenderer,
        storage: LocalStorage,
        tmp_path: Path,
    ) -> None:
        """The row says a copy was stored and storage disagrees — a lifecycle
        rule, a wiped directory. Rendering is the right answer to all of them."""
        await authed_client.get(f"/scans/{scan_id}/report")
        for pdf in tmp_path.rglob("*.pdf"):
            pdf.unlink()

        response = await authed_client.get(f"/scans/{scan_id}/report")

        assert response.status_code == 200
        assert renderer.calls == 2


class TestWithoutStorage:
    """The suite's default is NullStorage, which refuses."""

    async def test_the_report_is_still_served(
        self, authed_client: AsyncClient, scan_id: str, renderer: CountingRenderer
    ) -> None:
        """A cache entry is not the user's data. Nothing is lost when it is not
        written, so a misconfigured bucket must not take the endpoint offline."""
        response = await authed_client.get(f"/scans/{scan_id}/report")

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")

    async def test_every_request_renders(
        self, authed_client: AsyncClient, scan_id: str, renderer: CountingRenderer
    ) -> None:
        await authed_client.get(f"/scans/{scan_id}/report")
        await authed_client.get(f"/scans/{scan_id}/report")

        assert renderer.calls == 2

    async def test_no_key_is_recorded_for_an_object_that_was_not_stored(
        self,
        authed_client: AsyncClient,
        scan_id: str,
        session: AsyncSession,
        renderer: CountingRenderer,
    ) -> None:
        """Recording one would make every later request take the miss path
        *through* storage, which is slower than never having cached."""
        await authed_client.get(f"/scans/{scan_id}/report")

        session.expire_all()
        scan = await session.get(Scan, uuid.UUID(scan_id))
        assert scan.report_key is None

    async def test_the_failure_is_logged_rather_than_swallowed(
        self,
        authed_client: AsyncClient,
        scan_id: str,
        renderer: CountingRenderer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Silently degrading to no cache at all is how a misconfiguration
        survives to production unnoticed."""
        with caplog.at_level("WARNING", logger="app.services.report_cache"):
            await authed_client.get(f"/scans/{scan_id}/report")

        assert any("could not store" in record.message for record in caplog.records)

    async def test_null_storage_refuses_rather_than_reporting_a_miss(self) -> None:
        """If it returned None the cache would miss forever in silence."""
        with pytest.raises(StorageUnavailable):
            await get_storage().download("scans/whatever/report.pdf")


def _text(pdf: bytes) -> str:
    import io

    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
