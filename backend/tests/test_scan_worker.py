"""Tests for queueing a scan and for the worker that runs it.

The task itself is split so these can exercise the logic directly: `run_scan`
only opens a session, and `execute_scan` takes one — which is what lets the
transactional fixture keep these isolated.
"""

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SCAN_CATEGORIES, Finding, Project, Scan, ScanStatus, User
from app.scanners import registry
from app.services import scan_service
from app.utils.queue import InMemoryQueue
from app.workers.scan_tasks import execute_scan
from tests.helpers import CloneSettings, commit_all, init_repo, reload_scan

PROJECT = {"name": "api", "repository_url": "https://github.com/acme/api"}


@pytest.fixture
async def owned_project(session: AsyncSession) -> Project:
    user = User(email="owner@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, name="api", repository_url="https://github.com/acme/api")
    session.add(project)
    await session.commit()
    return project


class TestQueueingOnCreate:
    async def test_publishes_a_job(
        self, session: AsyncSession, owned_project: Project, queue: InMemoryQueue
    ) -> None:
        user = await session.get(User, owned_project.user_id)

        await scan_service.create_scan(session, owner=user, project_id=owned_project.id)

        assert len(queue.published) == 1
        task, payload = queue.published[0]
        assert task == "run_scan"

    async def test_job_carries_the_scan_id(
        self, session: AsyncSession, owned_project: Project, queue: InMemoryQueue
    ) -> None:
        user = await session.get(User, owned_project.user_id)

        scan = await scan_service.create_scan(session, owner=user, project_id=owned_project.id)

        _, payload = queue.published[0]
        assert payload["scan_id"] == str(scan.id)

    async def test_job_id_is_derived_from_the_scan(
        self, session: AsyncSession, owned_project: Project, queue: InMemoryQueue
    ) -> None:
        """Deduplication key. A double-clicked Run scan, or a retried publish,
        must not run the same repository twice."""
        user = await session.get(User, owned_project.user_id)

        scan = await scan_service.create_scan(session, owner=user, project_id=owned_project.id)

        _, payload = queue.published[0]
        assert payload["_job_id"] == f"scan:{scan.id}"

    async def test_nothing_is_published_for_another_users_project(
        self, session: AsyncSession, owned_project: Project, queue: InMemoryQueue
    ) -> None:
        intruder = User(email="intruder@example.com", password_hash="x")
        session.add(intruder)
        await session.commit()

        result = await scan_service.create_scan(
            session, owner=intruder, project_id=owned_project.id
        )

        assert result is None
        assert queue.published == []

    async def test_a_queue_failure_marks_the_scan_failed(
        self, session: AsyncSession, owned_project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The row is committed before publishing, so an unqueued scan would
        otherwise sit pending forever with the client polling it."""

        class BrokenQueue:
            async def publish(self, task: str, /, **payload: object) -> str:
                raise ConnectionError("redis is down")

        monkeypatch.setattr("app.services.scan_service.get_queue", lambda: BrokenQueue())
        user = await session.get(User, owned_project.user_id)
        # Read before expiring: afterwards this attribute would trigger a reload.
        project_id = owned_project.id

        with pytest.raises(ConnectionError):
            await scan_service.create_scan(session, owner=user, project_id=project_id)

        session.expire_all()
        scan = await session.scalar(select(Scan).where(Scan.project_id == project_id))
        assert scan is not None
        assert scan.status is ScanStatus.FAILED

    async def test_the_endpoint_still_returns_pending(
        self, authed_client: AsyncClient, queue: InMemoryQueue
    ) -> None:
        """Queueing must not delay the response or change its shape."""
        project = (await authed_client.post("/projects", json=PROJECT)).json()

        response = await authed_client.post(f"/projects/{project['id']}/scans")

        assert response.status_code == 201
        assert response.json()["status"] == "pending"
        assert len(queue.published) == 1


class TestExecuteScan:
    """The full pipeline: claim, clone, detect, scan, score, complete.

    Runs against a real git repository built in tmp_path, so the clone is
    genuine rather than mocked — the interesting failures in this code are all
    at the boundary between the worker and the filesystem.
    """

    @pytest.fixture
    def source_repo(self, tmp_path: Path) -> Path:
        repo = init_repo(tmp_path / "source")
        # Deliberately imperfect: no README and no tests, so the architecture
        # scanner has something to report and the score is not a flat 20.
        (repo / "pyproject.toml").write_text('[project]\nname="x"\n', encoding="utf-8")
        (repo / "uv.lock").write_text("", encoding="utf-8")
        (repo / "app").mkdir()
        (repo / "app" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        commit_all(repo)
        return repo

    @pytest.fixture(autouse=True)
    def clone_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Clone into tmp_path rather than the configured repos/ directory."""
        monkeypatch.setattr(
            "app.workers.repo.get_settings",
            lambda: CloneSettings(tmp_path / "clones"),
        )

    @pytest.fixture
    async def scan_of(self, session: AsyncSession, source_repo: Path) -> tuple[Scan, Project]:
        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(user_id=user.id, name="api", repository_url=source_repo.as_uri())
        session.add(project)
        await session.flush()
        scan = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
        session.add(scan)
        await session.commit()
        return scan, project

    async def test_completes_the_scan(
        self, session: AsyncSession, scan_of: tuple[Scan, Project]
    ) -> None:
        scan, _ = scan_of

        await execute_scan(session, scan_id=scan.id)

        assert (await reload_scan(session, scan.id)).status is ScanStatus.COMPLETED

    async def test_a_repository_with_no_source_scores_zero(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """Every scanner stays silent rather than guessing, which is right per
        check — but summed up, an *empty* repository scored 77/100, beating a
        real project. Silence must not be priced as excellence: no source code
        means no category reports and the score is zero, with the categories-
        reported count telling the user why."""
        repo = init_repo(tmp_path / "empty")
        (repo / "README.md").write_text("# just a readme\n", encoding="utf-8")
        commit_all(repo)

        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(user_id=user.id, name="empty", repository_url=repo.as_uri())
        session.add(project)
        await session.flush()
        scan = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
        session.add(scan)
        await session.commit()

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.status is ScanStatus.COMPLETED
        assert finished.score == 0
        assert set(finished.category_status.values()) == {"failed"}
        assert finished.category_scores == {}

    async def test_records_the_detected_framework(
        self, session: AsyncSession, scan_of: tuple[Scan, Project]
    ) -> None:
        """Project.framework is null until a scan fills it in."""
        scan, project = scan_of
        project_id = project.id

        await execute_scan(session, scan_id=scan.id)

        session.expire_all()
        refreshed = await session.get(Project, project_id)
        assert refreshed.framework == "Python"

    async def test_only_the_built_scanners_report(
        self, session: AsyncSession, scan_of: tuple[Scan, Project]
    ) -> None:
        """Categories with no scanner cost their full weight, which is honest —
        nothing assessed them."""
        scan, _ = scan_of

        await execute_scan(session, scan_id=scan.id)

        statuses = (await reload_scan(session, scan.id)).category_status
        reported = {c for c, s in statuses.items() if s == "completed"}

        # Derived from the registry so this keeps passing as scanners land,
        # while still failing if one stops reporting.
        assert reported == set(registry.SCANNERS)
        assert set(statuses) - reported == set(SCAN_CATEGORIES) - set(registry.SCANNERS)

    async def test_persists_the_findings(
        self, session: AsyncSession, scan_of: tuple[Scan, Project]
    ) -> None:
        scan, _ = scan_of

        await execute_scan(session, scan_id=scan.id)

        findings = (await session.scalars(select(Finding).where(Finding.scan_id == scan.id))).all()
        assert findings
        assert {f.category for f in findings} <= set(registry.SCANNERS)
        assert all(f.score_impact > 0 for f in findings)

    async def test_score_reflects_only_what_reported(
        self, session: AsyncSession, scan_of: tuple[Scan, Project]
    ) -> None:
        """Concrete numbers rather than derived ones, so an accidental change to
        any weight or impact fails here.

        The fixture repo has no README and no tests, so architecture loses 10 of
        its 20. No Dockerfile and no CI, so deployment loses all 15.
        Observability loses 6 of 10 for having no logging — the other two
        observability checks are service-only and it is detected as plain
        Python. Reliability scores full marks for the same reason: no health
        check is expected of a library, and it makes no outbound calls and
        swallows no errors. Scalability scores full marks because every one of
        its checks is service-only, so a library reports nothing.

        Security also scores clean: no credential files, no secrets, no debug
        flag, no TLS overrides, and the fixture declares no env-file usage for
        the .gitignore check to care about. With all six scanners registered,
        every category reports and a spotless repo really can reach 100.
        """
        scan, _ = scan_of

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.score == 69
        assert finished.scoring_version == "v1"

    async def test_records_what_each_category_scored(
        self, session: AsyncSession, scan_of: tuple[Scan, Project]
    ) -> None:
        """Only the categories that reported, and their points must add up to
        the total — otherwise the chart and the headline number disagree."""
        scan, _ = scan_of

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.category_scores == {
            "security": 25,
            "architecture": 10,
            "deployment": 0,
            "reliability": 20,
            "observability": 4,
            "scalability": 10,
        }
        assert sum(finished.category_scores.values()) == finished.score

    async def test_a_clone_failure_fails_the_scan(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """Not completed-with-zero: nothing was assessed, so there is no score
        to report."""
        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(
            user_id=user.id,
            name="gone",
            repository_url=(tmp_path / "does-not-exist").as_uri(),
        )
        session.add(project)
        await session.flush()
        scan = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
        session.add(scan)
        await session.commit()

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.status is ScanStatus.FAILED
        assert finished.score is None

    async def test_a_scanner_crash_fails_only_its_category(
        self,
        session: AsyncSession,
        scan_of: tuple[Scan, Project],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The partial-failure behaviour the whole design is built around."""
        scan, _ = scan_of

        monkeypatch.setattr(
            "app.scanners.registry.SCANNERS",
            {"architecture": _CrashingScanner()},
        )

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.status is ScanStatus.COMPLETED
        assert finished.category_status["architecture"] == "failed"
        assert finished.score == 0

    async def test_a_second_delivery_is_a_no_op(
        self, session: AsyncSession, scan_of: tuple[Scan, Project]
    ) -> None:
        """arq redelivers on retry; a duplicate must not rescan."""
        scan, _ = scan_of
        await execute_scan(session, scan_id=scan.id)
        first = await reload_scan(session, scan.id)

        await execute_scan(session, scan_id=scan.id)

        second = await reload_scan(session, scan.id)
        assert second.score == first.score
        findings = (await session.scalars(select(Finding).where(Finding.scan_id == scan.id))).all()
        assert len(findings) == len({f.title for f in findings})

    async def test_unknown_scan_is_a_no_op(self, session: AsyncSession) -> None:
        await execute_scan(session, scan_id=uuid.uuid4())


class _CrashingScanner:
    category = "architecture"

    def scan(self, repo_path: Path) -> list:
        raise RuntimeError("scanner exploded")
