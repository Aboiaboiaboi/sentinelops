"""Tests for the scan and finding endpoints.

The response shape assertions here are the ones that matter most: GET /scans/{id}
is polled on a timer, and a missing or mis-cased field would break the client's
polling loop or its status badge rather than raising anything visible.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app as fastapi_app
from app.models import SCAN_CATEGORIES, Finding, Scan, Severity

PROJECT = {"name": "sentinelops-api", "repository_url": "https://github.com/acme/sentinelops-api"}


@pytest.fixture
async def project_id(authed_client: AsyncClient) -> str:
    return (await authed_client.post("/projects", json=PROJECT)).json()["id"]


@pytest.fixture
async def other_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/signup",
            json={"email": "intruder@example.com", "password": "correct horse battery"},
        )
        yield client


class TestCreateScan:
    async def test_returns_a_pending_scan_immediately(
        self, authed_client: AsyncClient, project_id: str
    ) -> None:
        response = await authed_client.post(f"/projects/{project_id}/scans")

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["project_id"] == project_id

    async def test_response_has_every_contract_field(
        self, authed_client: AsyncClient, project_id: str
    ) -> None:
        body = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        assert set(body) == {
            "id",
            "project_id",
            "status",
            "score",
            "scoring_version",
            "category_status",
            "created_at",
        }

    async def test_score_and_version_are_null_not_missing(
        self, authed_client: AsyncClient, project_id: str
    ) -> None:
        body = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        assert body["score"] is None
        assert body["scoring_version"] is None

    async def test_seeds_every_category_as_pending(
        self, authed_client: AsyncClient, project_id: str
    ) -> None:
        """The client renders one bar per entry, so an empty map would show a
        scan with no categories rather than six waiting to start."""
        body = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        assert set(body["category_status"]) == set(SCAN_CATEGORIES)
        assert set(body["category_status"].values()) == {"pending"}

    async def test_status_is_lowercase_on_the_wire(
        self, authed_client: AsyncClient, project_id: str
    ) -> None:
        """The client compares against the literal 'completed'/'failed' to decide
        when to stop polling, so casing is load-bearing."""
        body = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        assert body["status"] == body["status"].lower()

    async def test_created_at_is_z_suffixed(
        self, authed_client: AsyncClient, project_id: str
    ) -> None:
        body = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        assert body["created_at"].endswith("Z")

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.post(f"/projects/{uuid.uuid4()}/scans")

        assert response.status_code == 401

    async def test_unknown_project_is_404(self, authed_client: AsyncClient) -> None:
        response = await authed_client.post(f"/projects/{uuid.uuid4()}/scans")

        assert response.status_code == 404

    async def test_cannot_scan_another_users_project(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        theirs = (await other_client.post("/projects", json=PROJECT)).json()

        response = await authed_client.post(f"/projects/{theirs['id']}/scans")

        assert response.status_code == 404


class TestListScans:
    async def test_empty_for_a_project_with_no_scans(
        self, authed_client: AsyncClient, project_id: str
    ) -> None:
        response = await authed_client.get(f"/projects/{project_id}/scans")

        assert response.status_code == 200
        assert response.json() == []

    async def test_unknown_project_is_404_not_an_empty_list(
        self, authed_client: AsyncClient
    ) -> None:
        """A project with no scans and a project that is not yours must not look
        the same."""
        response = await authed_client.get(f"/projects/{uuid.uuid4()}/scans")

        assert response.status_code == 404

    async def test_returns_newest_first(
        self, authed_client: AsyncClient, project_id: str, session: AsyncSession
    ) -> None:
        first = (await authed_client.post(f"/projects/{project_id}/scans")).json()
        second = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        ids = [s["id"] for s in (await authed_client.get(f"/projects/{project_id}/scans")).json()]

        assert ids == [second["id"], first["id"]]

    async def test_excludes_other_users_scans(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        theirs = (await other_client.post("/projects", json=PROJECT)).json()
        await other_client.post(f"/projects/{theirs['id']}/scans")

        mine = (await authed_client.post("/projects", json=PROJECT)).json()
        listed = (await authed_client.get(f"/projects/{mine['id']}/scans")).json()

        assert listed == []


class TestGetScan:
    async def test_returns_the_scan(self, authed_client: AsyncClient, project_id: str) -> None:
        created = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        response = await authed_client.get(f"/scans/{created['id']}")

        assert response.status_code == 200
        assert response.json() == created

    async def test_unknown_scan_is_404(self, authed_client: AsyncClient) -> None:
        response = await authed_client.get(f"/scans/{uuid.uuid4()}")

        assert response.status_code == 404

    async def test_another_users_scan_is_404(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        theirs = (await other_client.post("/projects", json=PROJECT)).json()
        their_scan = (await other_client.post(f"/projects/{theirs['id']}/scans")).json()

        response = await authed_client.get(f"/scans/{their_scan['id']}")

        assert response.status_code == 404

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.get(f"/scans/{uuid.uuid4()}")

        assert response.status_code == 401


class TestListFindings:
    async def _seed_findings(self, session: AsyncSession, scan_id: str) -> None:
        scan = await session.scalar(select(Scan).where(Scan.id == uuid.UUID(scan_id)))
        assert scan is not None
        session.add_all(
            [
                Finding(
                    scan_id=scan.id,
                    category="security",
                    severity=severity,
                    title=f"{severity.value} issue",
                    description="description",
                    recommendation="recommendation",
                    score_impact=impact,
                )
                for severity, impact in (
                    (Severity.LOW, 2),
                    (Severity.CRITICAL, 10),
                    (Severity.MEDIUM, 4),
                    (Severity.HIGH, 6),
                )
            ]
        )
        await session.commit()

    async def test_empty_for_a_scan_with_no_findings(
        self, authed_client: AsyncClient, project_id: str
    ) -> None:
        scan = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        response = await authed_client.get(f"/scans/{scan['id']}/findings")

        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_most_severe_first(
        self, authed_client: AsyncClient, project_id: str, session: AsyncSession
    ) -> None:
        scan = (await authed_client.post(f"/projects/{project_id}/scans")).json()
        await self._seed_findings(session, scan["id"])

        body = (await authed_client.get(f"/scans/{scan['id']}/findings")).json()

        assert [f["severity"] for f in body] == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

    async def test_severity_is_uppercase_on_the_wire(
        self, authed_client: AsyncClient, project_id: str, session: AsyncSession
    ) -> None:
        """The only uppercase enum in the contract — the client uses it directly
        as a lookup key for badge styling."""
        scan = (await authed_client.post(f"/projects/{project_id}/scans")).json()
        await self._seed_findings(session, scan["id"])

        body = (await authed_client.get(f"/scans/{scan['id']}/findings")).json()

        assert all(f["severity"] == f["severity"].upper() for f in body)

    async def test_finding_has_every_contract_field(
        self, authed_client: AsyncClient, project_id: str, session: AsyncSession
    ) -> None:
        scan = (await authed_client.post(f"/projects/{project_id}/scans")).json()
        await self._seed_findings(session, scan["id"])

        body = (await authed_client.get(f"/scans/{scan['id']}/findings")).json()

        assert set(body[0]) == {
            "id",
            "scan_id",
            "category",
            "severity",
            "title",
            "description",
            "recommendation",
            "score_impact",
        }

    async def test_unknown_scan_is_404(self, authed_client: AsyncClient) -> None:
        response = await authed_client.get(f"/scans/{uuid.uuid4()}/findings")

        assert response.status_code == 404

    async def test_another_users_findings_are_404(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        theirs = (await other_client.post("/projects", json=PROJECT)).json()
        their_scan = (await other_client.post(f"/projects/{theirs['id']}/scans")).json()

        response = await authed_client.get(f"/scans/{their_scan['id']}/findings")

        assert response.status_code == 404

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.get(f"/scans/{uuid.uuid4()}/findings")

        assert response.status_code == 401


class TestCascade:
    async def test_deleting_a_project_removes_its_scans_and_findings(
        self, authed_client: AsyncClient, project_id: str, session: AsyncSession
    ) -> None:
        scan = (await authed_client.post(f"/projects/{project_id}/scans")).json()

        await authed_client.delete(f"/projects/{project_id}")

        assert (await authed_client.get(f"/scans/{scan['id']}")).status_code == 404
