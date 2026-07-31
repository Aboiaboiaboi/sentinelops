"""Tests for scan-to-scan comparison.

The refusals carry the weight. A comparison that renders a misleading delta is
worse than one that declines: the two ways a delta can lie are a rubric change
between the scans, and a category that stopped being assessed rather than
getting worse.
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Scan, ScanStatus, User
from app.services import scan_service
from app.services.comparison_service import compare
from app.workers.scan_tasks import execute_scan
from tests.helpers import CloneSettings, commit_all, init_repo, reload_scan

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _scan(
    *,
    score: int | None = 60,
    status: ScanStatus = ScanStatus.COMPLETED,
    scoring_version: str | None = "v1",
    category_scores: dict | None = None,
    check_results: list | None = None,
    created_at: datetime = NOW,
) -> Scan:
    """A Scan built in memory — comparison never touches the database."""
    return Scan(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=status,
        score=score,
        scoring_version=scoring_version,
        category_status={},
        category_scores=category_scores if category_scores is not None else {},
        check_results=check_results if check_results is not None else [],
        created_at=created_at,
    )


def _check(check_id: str, outcome: str, *, category: str = "security", title: str = "A check"):
    return {"id": check_id, "category": category, "title": title, "outcome": outcome}


class TestNothingToCompare:
    def test_the_first_scan_of_a_project_has_no_predecessor(self) -> None:
        result = compare(None, _scan())

        assert result.previous_scan_id is None
        assert result.score_delta is None

    def test_a_failed_current_scan_has_no_score_to_compare(self) -> None:
        result = compare(_scan(), _scan(status=ScanStatus.FAILED, score=None))

        assert result.previous_scan_id is None

    def test_a_failed_previous_scan_is_not_a_baseline(self) -> None:
        result = compare(_scan(status=ScanStatus.FAILED, score=None), _scan())

        assert result.previous_scan_id is None


class TestScoringVersionRefusal:
    def test_a_rubric_change_refuses_the_delta(self) -> None:
        """Weights change between versions, so the difference would measure a
        change in SentinelOps and attribute it to the repository."""
        previous = _scan(score=40, scoring_version="v1")
        current = _scan(score=80, scoring_version="v2")

        result = compare(previous, current)

        assert result.comparable is False
        assert result.score_delta is None
        assert result.categories == []

    def test_it_still_says_which_scan_and_why(self) -> None:
        """Refusing is not the same as saying nothing — the user gets the
        earlier scan's identity and an explanation."""
        previous = _scan(score=40, scoring_version="v1")
        current = _scan(score=80, scoring_version="v2")

        result = compare(previous, current)

        assert result.previous_scan_id == previous.id
        assert result.previous_score == 40
        assert "v1" in result.reason and "v2" in result.reason

    def test_the_same_version_compares_normally(self) -> None:
        result = compare(_scan(score=40), _scan(score=52))

        assert result.comparable is True
        assert result.score_delta == 12


class TestScoreAndCategories:
    def test_an_improvement_is_positive(self) -> None:
        assert compare(_scan(score=51), _scan(score=61)).score_delta == 10

    def test_a_regression_is_negative(self) -> None:
        assert compare(_scan(score=61), _scan(score=51)).score_delta == -10

    def test_category_deltas_are_reported_per_category(self) -> None:
        previous = _scan(category_scores={"security": 15, "deployment": 0})
        current = _scan(category_scores={"security": 25, "deployment": 4})

        deltas = {c.category: c.delta for c in compare(previous, current).categories}

        assert deltas == {"security": 10, "deployment": 4}

    def test_a_category_that_stopped_being_assessed_is_not_a_regression(self) -> None:
        """The case the all-skipped rule created: a repository that stops
        looking like a service drops scalability entirely. Reporting that as
        minus its full weight would accuse it of breaking something."""
        previous = _scan(category_scores={"security": 25, "scalability": 10})
        current = _scan(category_scores={"security": 25})

        scalability = next(
            c for c in compare(previous, current).categories if c.category == "scalability"
        )

        assert scalability.delta is None
        assert scalability.previous == 10
        assert scalability.current is None

    def test_a_newly_assessed_category_is_also_not_a_delta(self) -> None:
        previous = _scan(category_scores={"security": 25})
        current = _scan(category_scores={"security": 25, "scalability": 10})

        scalability = next(
            c for c in compare(previous, current).categories if c.category == "scalability"
        )

        assert scalability.delta is None
        assert scalability.previous is None


class TestCheckChanges:
    def test_reports_only_checks_whose_outcome_moved(self) -> None:
        previous = _scan(
            check_results=[_check("security.tls", "failed"), _check("security.debug", "passed")]
        )
        current = _scan(
            check_results=[_check("security.tls", "passed"), _check("security.debug", "passed")]
        )

        changes = compare(previous, current).checks

        assert [c.id for c in changes] == ["security.tls"]
        assert changes[0].previous_outcome == "failed"
        assert changes[0].current_outcome == "passed"

    def test_regressions_come_before_improvements(self) -> None:
        """What broke is the thing somebody needs to see first."""
        previous = _scan(
            check_results=[_check("security.a", "failed"), _check("security.b", "passed")]
        )
        current = _scan(
            check_results=[_check("security.a", "passed"), _check("security.b", "failed")]
        )

        changes = compare(previous, current).checks

        assert [c.id for c in changes] == ["security.b", "security.a"]
        assert changes[0].is_regression
        assert changes[1].is_improvement

    def test_a_check_added_since_the_last_scan_is_not_a_change(self) -> None:
        """It was added by us, not changed by the repository — presenting it as
        a change would credit or blame somebody for a SentinelOps release."""
        previous = _scan(check_results=[_check("security.a", "passed")])
        current = _scan(
            check_results=[_check("security.a", "passed"), _check("security.brand_new", "failed")]
        )

        assert compare(previous, current).checks == []

    def test_a_move_between_passed_and_skipped_is_neither(self) -> None:
        """Reported, because it explains why a category's score moved — but not
        counted as a regression or a fix."""
        previous = _scan(check_results=[_check("reliability.health", "passed")])
        current = _scan(check_results=[_check("reliability.health", "skipped")])

        change = compare(previous, current).checks[0]

        assert not change.is_regression
        assert not change.is_improvement

    def test_scans_predating_check_results_compare_on_score_alone(self) -> None:
        """Old rows have an empty list; the score and category deltas still
        work, and the check list is simply empty."""
        previous = _scan(check_results=[], category_scores={"security": 15})
        current = _scan(check_results=[], category_scores={"security": 25})

        result = compare(previous, current)

        assert result.checks == []
        assert result.categories[0].delta == 10


class TestEndpoint:
    async def _project_with_scans(
        self, session: AsyncSession, count: int
    ) -> tuple[Project, list[Scan]]:
        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(user_id=user.id, name="api", repository_url="https://example.com/x")
        session.add(project)
        await session.flush()
        scans = []
        for index in range(count):
            scan = Scan(
                project_id=project.id,
                status=ScanStatus.COMPLETED,
                score=50 + index * 5,
                scoring_version="v1",
                category_status={},
                category_scores={"security": 15 + index},
                check_results=[],
                created_at=NOW + timedelta(minutes=index),
            )
            session.add(scan)
            scans.append(scan)
        await session.commit()
        return project, scans

    async def test_picks_the_most_recent_earlier_completed_scan(
        self, session: AsyncSession
    ) -> None:
        _, scans = await self._project_with_scans(session, 3)

        previous = await scan_service.get_previous_completed_scan(db=session, scan=scans[2])

        assert previous is not None
        assert previous.id == scans[1].id

    async def test_the_earliest_scan_has_no_predecessor(self, session: AsyncSession) -> None:
        _, scans = await self._project_with_scans(session, 2)

        assert await scan_service.get_previous_completed_scan(db=session, scan=scans[0]) is None

    async def test_the_response_has_every_contract_field(self, authed_client: AsyncClient) -> None:
        created = await authed_client.post(
            "/projects", json={"name": "x", "repository_url": "https://github.com/a/b"}
        )
        scan_id = (await authed_client.post(f"/projects/{created.json()['id']}/scans")).json()["id"]

        body = (await authed_client.get(f"/scans/{scan_id}/comparison")).json()

        assert set(body) == {
            "previous_scan_id",
            "previous_created_at",
            "previous_score",
            "comparable",
            "reason",
            "score_delta",
            "categories",
            "checks",
        }

    async def test_a_lone_scan_reports_nothing_to_compare_rather_than_404(
        self, authed_client: AsyncClient
    ) -> None:
        """The first scan of a project is a normal state, not an error — the
        client renders no comparison rather than handling a failure."""
        created = await authed_client.post(
            "/projects", json={"name": "x", "repository_url": "https://github.com/a/b"}
        )
        scan_id = (await authed_client.post(f"/projects/{created.json()['id']}/scans")).json()["id"]

        response = await authed_client.get(f"/scans/{scan_id}/comparison")

        assert response.status_code == 200
        assert response.json()["previous_scan_id"] is None

    async def test_another_users_scan_is_a_404(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        created = await authed_client.post(
            "/projects", json={"name": "x", "repository_url": "https://github.com/a/b"}
        )
        scan_id = (await authed_client.post(f"/projects/{created.json()['id']}/scans")).json()["id"]

        assert (await other_client.get(f"/scans/{scan_id}/comparison")).status_code == 404


class TestAgainstRealScans:
    async def test_two_real_scans_of_the_same_repository_compare(
        self, session: AsyncSession, tmp_path, monkeypatch
    ) -> None:
        """End to end through the worker, so the comparison reads exactly what
        the pipeline writes."""
        repo = init_repo(tmp_path / "source")
        (repo / "app").mkdir()
        (repo / "app" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        commit_all(repo)
        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(user_id=user.id, name="api", repository_url=repo.as_uri())
        session.add(project)
        await session.flush()
        # Captured before the loop: reload_scan() expires every object in the
        # session, so reading project.id on the second pass would trigger a
        # lazy refresh from sync code and raise MissingGreenlet.
        project_id = project.id

        finished = []
        for index in range(2):
            scan = Scan(
                project_id=project_id,
                category_status=scan_service.initial_category_status(),
                created_at=NOW + timedelta(minutes=index),
            )
            session.add(scan)
            await session.commit()
            await execute_scan(session, scan_id=scan.id)
            finished.append(await reload_scan(session, scan.id))

        previous = await scan_service.get_previous_completed_scan(db=session, scan=finished[1])
        result = compare(previous, finished[1])

        assert result.comparable is True
        # Same repository scanned twice: nothing moved.
        assert result.score_delta == 0
        assert result.checks == []
        assert all(delta.delta == 0 for delta in result.categories)
