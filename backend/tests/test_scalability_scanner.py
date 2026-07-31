"""Tests for the scalability scanner.

The false-positive cases carry most of the weight here. Every check in this
category is one regex away from firing on a perfectly good repository — Flask's
cookie sessions, a module-level lookup table, an ORM that pools by default —
and a category that cries wolf on healthy code is worse than one that does not
exist.
"""

from pathlib import Path

import pytest

from app.scanners.base import RepositoryIndex, Severity, findings_of
from app.scanners.scalability import ScalabilityScanner

SCANNER = ScalabilityScanner()


def _scan(repo: Path, framework: str | None = "FastAPI"):
    """The findings from a scan; check outcomes are covered in
    test_check_results.py."""
    return findings_of(SCANNER.scan(RepositoryIndex.build(repo, framework=framework)))


def _titles(repo: Path, framework: str | None = "FastAPI") -> set[str]:
    return {f.title for f in _scan(repo, framework)}


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


HEALTHY_APP = """
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

app = FastAPI()
engine = create_async_engine(DATABASE_URL)

WEIGHTS = {"security": 25, "reliability": 20}

@app.get("/items")
async def items():
    return await load_items()
"""


@pytest.fixture
def healthy_repo(tmp_path: Path) -> Path:
    _write(tmp_path, "pyproject.toml", "[project]\ndependencies = ['fastapi']\n")
    _write(tmp_path, "app/main.py", HEALTHY_APP)
    return tmp_path


class TestHealthyRepository:
    def test_produces_no_findings(self, healthy_repo: Path) -> None:
        assert _scan(healthy_repo) == []

    def test_every_finding_belongs_to_this_category(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "@app.get('/x')\ndef x(): pass\ncache = {}\n")

        assert {f.category for f in _scan(tmp_path)} == {"scalability"}

    def test_impacts_cannot_exceed_the_category_weight(self, tmp_path: Path) -> None:
        """A repository failing everything scores the category zero, not below."""
        _write(tmp_path, "package.json", '{"dependencies": {"express-session": "1.0.0"}}')
        _write(
            tmp_path,
            "server.js",
            "const app = express();\nconst sessions = {};\n"
            "app.get('/x', () => {});\n"
            "const UPLOAD_DIR = './uploads';\n"
            "const c = new pg.Client();\n",
        )

        assert sum(f.score_impact for f in _scan(tmp_path)) <= 10


class TestOnlyAppliesToServices:
    def test_a_library_is_never_flagged(self, tmp_path: Path) -> None:
        """Nothing here applies to something with no instances behind a load
        balancer. A CLI holding a module-level dict is just a program."""
        _write(tmp_path, "cli.py", "cache = {}\n@app.get('/x')\ndef x(): pass\n")

        assert _scan(tmp_path, framework=None) == []

    def test_an_undetected_framework_is_not_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "main.rs", "let mut cache = HashMap::new();\n")

        assert _scan(tmp_path, framework="Rust") == []


class TestInMemoryState:
    def test_flags_express_session_with_no_store(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", '{"dependencies": {"express-session": "^1.17.0"}}')

        assert "State is kept in process memory" in _titles(tmp_path)

    def test_is_high_severity(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", '{"dependencies": {"express-session": "^1.17.0"}}')
        finding = next(f for f in _scan(tmp_path) if f.title.startswith("State is kept"))

        assert finding.severity is Severity.HIGH

    @pytest.mark.parametrize(
        "store",
        ["connect-redis", "connect-mongo", "connect-pg-simple", "session-file-store"],
    )
    def test_accepts_an_external_session_store(self, tmp_path: Path, store: str) -> None:
        _write(
            tmp_path,
            "package.json",
            f'{{"dependencies": {{"express-session": "^1.17.0", "{store}": "^7.0.0"}}}}',
        )

        assert "State is kept in process memory" not in _titles(tmp_path)

    def test_flask_cookie_sessions_are_not_flagged(self, tmp_path: Path) -> None:
        """Flask's default session is a signed cookie held by the client, which
        survives any number of instances."""
        _write(tmp_path, "app.py", "from flask import session\n@app.route('/x')\ndef x(): pass\n")

        assert "State is kept in process memory" not in _titles(tmp_path)

    def test_django_sessions_are_not_flagged(self, tmp_path: Path) -> None:
        """Django's default session backend is the database."""
        _write(tmp_path, "views.py", "def view(request):\n    request.session['k'] = 1\n")

        assert "State is kept in process memory" not in _titles(tmp_path)

    def test_flags_an_empty_module_level_collection_in_a_route_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "api.py", "_sessions = {}\n\n@app.get('/x')\ndef x(): pass\n")

        assert "State is kept in process memory" in _titles(tmp_path)

    def test_a_populated_constant_is_not_state(self, tmp_path: Path) -> None:
        """`WEIGHTS = {"security": 25}` is a lookup table, not something request
        handlers accumulate into."""
        _write(tmp_path, "api.py", "WEIGHTS = {'security': 25}\n@app.get('/x')\ndef x(): pass\n")

        assert "State is kept in process memory" not in _titles(tmp_path)

    def test_an_empty_collection_outside_route_code_is_not_flagged(self, tmp_path: Path) -> None:
        """A registry or a default in a helper module is not per-request state."""
        _write(tmp_path, "app/main.py", HEALTHY_APP)
        _write(tmp_path, "helpers.py", "_registry = {}\n\ndef register(x): _registry[x] = 1\n")

        assert "State is kept in process memory" not in _titles(tmp_path)

    def test_an_indented_empty_collection_is_not_module_level(self, tmp_path: Path) -> None:
        """A local inside a function is per-request and disappears with it."""
        _write(tmp_path, "api.py", "@app.get('/x')\ndef x():\n    seen = {}\n    return seen\n")

        assert "State is kept in process memory" not in _titles(tmp_path)


class TestLocalFileStorage:
    @pytest.mark.parametrize("marker", ["UPLOAD_FOLDER", "MEDIA_ROOT", "uploadDir"])
    def test_flags_uploads_written_locally(self, tmp_path: Path, marker: str) -> None:
        _write(tmp_path, "config.py", f"{marker} = '/var/app/uploads'\n")

        assert "Uploaded files are written to local disk" in _titles(tmp_path)

    def test_is_medium_severity(self, tmp_path: Path) -> None:
        _write(tmp_path, "config.py", "UPLOAD_FOLDER = '/var/app/uploads'\n")
        finding = next(f for f in _scan(tmp_path) if f.title.startswith("Uploaded files"))

        assert finding.severity is Severity.MEDIUM

    @pytest.mark.parametrize(
        "library",
        [
            "boto3",
            "google-cloud-storage",
            "django-storages",
            # Scoped npm packages: a leading \b cannot match before "@", so
            # these silently matched nothing until the pattern was restructured.
            "@azure/storage-blob",
            "@aws-sdk/client-s3",
            "@google-cloud/storage",
        ],
    )
    def test_object_storage_anywhere_clears_the_check(self, tmp_path: Path, library: str) -> None:
        """Somebody using S3 has already solved this; a local write is staging."""
        _write(tmp_path, "requirements.txt", f"{library}\n")
        _write(tmp_path, "config.py", "UPLOAD_FOLDER = '/var/app/uploads'\n")

        assert "Uploaded files are written to local disk" not in _titles(tmp_path)

    def test_object_storage_in_source_also_counts(self, tmp_path: Path) -> None:
        _write(tmp_path, "config.py", "UPLOAD_FOLDER = '/var/app/uploads'\n")
        _write(tmp_path, "store.py", "import boto3\nclient = boto3.client('s3')\n")

        assert "Uploaded files are written to local disk" not in _titles(tmp_path)

    @pytest.mark.parametrize("path", ["/tmp/uploads", "./cache/files", "/var/scratch"])
    def test_an_ephemeral_destination_is_correct_use_of_disk(
        self, tmp_path: Path, path: str
    ) -> None:
        _write(tmp_path, "config.py", f"UPLOAD_FOLDER = '{path}'\n")

        assert "Uploaded files are written to local disk" not in _titles(tmp_path)


class TestDatabaseConnections:
    def test_flags_pooling_being_disabled(self, tmp_path: Path) -> None:
        _write(tmp_path, "db.py", "engine = create_engine(URL, poolclass=NullPool)\n")

        assert "Database connections are not pooled" in _titles(tmp_path)

    def test_flags_a_raw_connection_inside_a_route_file(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "api.py",
            "@app.get('/x')\ndef x():\n    conn = psycopg2.connect(DSN)\n    return conn\n",
        )

        assert "Database connections are not pooled" in _titles(tmp_path)

    def test_an_orm_with_no_pool_configuration_is_not_flagged(self, tmp_path: Path) -> None:
        """SQLAlchemy, Django and Rails all pool by default, so silence here is
        correct rather than an omission."""
        _write(tmp_path, "db.py", "engine = create_async_engine(DATABASE_URL)\n")

        assert "Database connections are not pooled" not in _titles(tmp_path)

    def test_a_raw_connection_outside_request_code_is_not_flagged(self, tmp_path: Path) -> None:
        """A migration or a management script connects once and exits."""
        _write(tmp_path, "migrate.py", "conn = psycopg2.connect(DSN)\nrun_migrations(conn)\n")

        assert "Database connections are not pooled" not in _titles(tmp_path)

    @pytest.mark.parametrize(
        "path",
        ["alembic/env.py", "migrations/env.py", "db/migrate/setup.py"],
    )
    def test_migration_tooling_may_disable_pooling(self, tmp_path: Path, path: str) -> None:
        """Found by scanning SentinelOps itself. Alembic's own generated env.py
        sets `poolclass=pool.NullPool`, and that is correct — a migration
        connects, runs and exits, so a pool would hold connections for nothing.
        """
        _write(tmp_path, path, "connectable = engine_from_config(cfg, poolclass=pool.NullPool)\n")

        assert "Database connections are not pooled" not in _titles(tmp_path)

    def test_pooling_disabled_in_service_code_is_still_flagged(self, tmp_path: Path) -> None:
        """The migration guard must not become a blanket exemption."""
        _write(tmp_path, "app/db.py", "engine = create_engine(URL, poolclass=NullPool)\n")

        assert "Database connections are not pooled" in _titles(tmp_path)

    def test_a_pooled_node_client_is_not_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "db.js", "const pool = new pg.Pool({ max: 20 });\napp.get('/x', fn);\n")

        assert "Database connections are not pooled" not in _titles(tmp_path)


class TestEvidenceMustBeCodeNotProse:
    """Found by scanning SentinelOps itself, which reported its own scanner as
    having unpooled connections because the pattern definitions contain the
    string being searched for. Repositories full of prose about these very
    patterns — linters, scanners, infrastructure tooling — are exactly the ones
    people point at a production-readiness checker.
    """

    @pytest.mark.parametrize("comment", ["# poolclass=NullPool", "// poolclass=NullPool"])
    def test_a_comment_about_pooling_is_not_disabled_pooling(
        self, tmp_path: Path, comment: str
    ) -> None:
        _write(tmp_path, "db.py", f"{comment}\nengine = create_async_engine(URL)\n")

        assert "Database connections are not pooled" not in _titles(tmp_path)

    def test_a_comment_about_uploads_is_not_an_upload(self, tmp_path: Path) -> None:
        _write(tmp_path, "notes.py", "# UPLOAD_FOLDER was removed in favour of S3\n")

        assert "Uploaded files are written to local disk" not in _titles(tmp_path)

    def test_real_code_on_the_next_line_is_still_found(self, tmp_path: Path) -> None:
        """Stripping comments must not stop the check working."""
        _write(
            tmp_path,
            "db.py",
            "# we disable pooling here\nengine = create_engine(URL, poolclass=NullPool)\n",
        )

        assert "Database connections are not pooled" in _titles(tmp_path)


class TestFindingQuality:
    def test_findings_name_the_file_they_came_from(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/api.py", "_sessions = {}\n@app.get('/x')\ndef x(): pass\n")
        finding = next(f for f in _scan(tmp_path) if f.title.startswith("State is kept"))

        assert "src/api.py" in finding.description

    def test_one_finding_per_problem_not_per_file(self, tmp_path: Path) -> None:
        for name in ("a.py", "b.py", "c.py"):
            _write(tmp_path, name, "_cache = {}\n@app.get('/x')\ndef x(): pass\n")

        state_findings = [f for f in _scan(tmp_path) if f.title.startswith("State is kept")]

        assert len(state_findings) == 1
        assert "2 other files" in state_findings[0].description

    def test_every_finding_has_a_recommendation(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", '{"dependencies": {"express-session": "1.0.0"}}')
        _write(tmp_path, "config.py", "UPLOAD_FOLDER = '/var/uploads'\n")
        _write(tmp_path, "db.py", "engine = create_engine(URL, poolclass=NullPool)\n")

        findings = _scan(tmp_path)

        assert len(findings) == 3
        assert all(f.recommendation for f in findings)
