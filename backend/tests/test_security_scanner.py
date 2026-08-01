"""Tests for the baseline security scanner.

The false-positive cases matter more here than in any other category. Telling
someone they leaked a credential when they did not is alarming, and the second
false alarm is when they stop believing the tool.

Fixture tokens are format-valid but assembled from repeated filler at runtime,
so no real-looking credential literal sits in this file — for push-protection
tooling, and for the scanner itself, whose placeholder guard would otherwise
rightly reject values spelling EXAMPLE or xxx into themselves.
"""

from pathlib import Path

import pytest

from app.scanners.base import CheckOutcome, RepositoryIndex, Severity, findings_of
from app.scanners.security import SecurityScanner

SCANNER = SecurityScanner()


def _scan(repo: Path):
    """The findings from a scan; check outcomes are covered in
    test_check_results.py."""
    return findings_of(SCANNER.scan(RepositoryIndex.build(repo, framework="FastAPI")))


def _titles(repo: Path) -> set[str]:
    return {f.title for f in _scan(repo)}


def _write(root: Path, name: str, content: str = "x") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def healthy_repo(tmp_path: Path) -> Path:
    """Env template committed, real env ignored, secrets from the environment."""
    _write(tmp_path, ".gitignore", "*.pyc\n.env\n.env.*\n!.env.example\n")
    _write(tmp_path, ".env.example", "DATABASE_URL=\nSECRET_KEY=\n")
    _write(tmp_path, "requirements.txt", "fastapi\npython-dotenv\n")
    _write(tmp_path, "app.py", "import os\nSECRET_KEY = os.environ['SECRET_KEY']\n")
    return tmp_path


class TestHealthyRepository:
    def test_produces_no_findings(self, healthy_repo: Path) -> None:
        assert _scan(healthy_repo) == []

    def test_every_finding_belongs_to_this_category(self, tmp_path: Path) -> None:
        _write(tmp_path, ".env", "SECRET=real\n")

        assert {f.category for f in _scan(tmp_path)} == {"security"}

    def test_impacts_cannot_exceed_the_category_weight(self, tmp_path: Path) -> None:
        _write(tmp_path, ".env", "DB_PASSWORD=r3al-lo0king-value\n")
        _write(tmp_path, "requirements.txt", "python-dotenv\n")
        _write(tmp_path, "app.py", "DEBUG = True\nr.get(u, verify=False)\n")
        _write(tmp_path, "Dockerfile", "FROM python:3.14\nENV DB_PASSWORD=hunter2hunter2\n")

        assert sum(f.score_impact for f in _scan(tmp_path)) <= 25


class TestCredentialFiles:
    @pytest.mark.parametrize("name", ["id_rsa", "id_ed25519", ".htpasswd"])
    def test_flags_files_that_are_credentials_by_definition(
        self, tmp_path: Path, name: str
    ) -> None:
        _write(tmp_path, name)

        assert "Credential files are committed" in _titles(tmp_path)

    @pytest.mark.parametrize("name", [".env", ".env.production"])
    def test_flags_an_env_file_holding_a_real_value(self, tmp_path: Path, name: str) -> None:
        _write(tmp_path, name, "DB_PASSWORD=prod-h8kQz94xLm\n")

        assert "Credential files are committed" in _titles(tmp_path)

    def test_is_critical(self, tmp_path: Path) -> None:
        _write(tmp_path, ".env", "DB_PASSWORD=prod-h8kQz94xLm\n")
        finding = next(f for f in _scan(tmp_path) if f.title.startswith("Credential"))

        assert finding.severity is Severity.CRITICAL

    def test_an_env_file_of_blanked_defaults_is_a_template(self, tmp_path: Path) -> None:
        """tiangolo's FastAPI template commits a .env where every secret is
        `changethis`, and Sentry self-hosted commits documented defaults. The
        name nominates; only a real value convicts."""
        _write(tmp_path, ".env", "SECRET_KEY=changethis\nSMTP_PASSWORD=\nRETENTION_DAYS=90\n")

        assert "Credential files are committed" not in _titles(tmp_path)

    def test_an_npmrc_interpolating_its_token_is_correct(self, tmp_path: Path) -> None:
        """Insomnia's .npmrc: registry settings plus ${NODE_AUTH_TOKEN}."""
        _write(
            tmp_path,
            ".npmrc",
            "engine-strict=true\n//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}\n",
        )

        assert "Credential files are committed" not in _titles(tmp_path)

    def test_an_npmrc_with_a_literal_token_is_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, ".npmrc", "//registry.npmjs.org/:_authToken=aGVsbG8td29ybGQtc2VjcmV0\n")

        assert "Credential files are committed" in _titles(tmp_path)

    def test_an_oauth_client_json_without_keys_is_not_a_credential(self, tmp_path: Path) -> None:
        _write(tmp_path, "credentials.json", '{"client_id": "abc.apps.googleusercontent.com"}\n')

        assert "Credential files are committed" not in _titles(tmp_path)

    def test_a_service_account_with_a_private_key_is(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "service-account.json",
            '{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----abcdef"}\n',
        )

        assert "Credential files are committed" in _titles(tmp_path)

    @pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template", ".env.dist"])
    def test_env_templates_are_the_correct_pattern(self, tmp_path: Path, name: str) -> None:
        _write(tmp_path, name, "SECRET_KEY=\n")

        assert "Credential files are committed" not in _titles(tmp_path)

    def test_a_private_key_pem_is_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "server.pem", "-----BEGIN RSA PRIVATE KEY-----\nabc\n")

        assert "Credential files are committed" in _titles(tmp_path)

    def test_a_public_certificate_pem_is_not(self, tmp_path: Path) -> None:
        """A certificate is supposed to be committed. Only the file's own header
        distinguishes it from a private key — never the extension."""
        _write(tmp_path, "ca-cert.pem", "-----BEGIN CERTIFICATE-----\nabc\n")

        assert "Credential files are committed" not in _titles(tmp_path)

    @pytest.mark.parametrize(
        "path",
        [
            "tests/fixtures/test.pem",
            # Insomnia's smoke-test certificates: a fixtures/ directory outside
            # any directory literally named tests/.
            "packages/smoke/fixtures/certificates/rootCA-key.pem",
            "src/__mocks__/fake-key.pem",
        ],
    )
    def test_a_test_fixture_key_is_a_fixture(self, tmp_path: Path, path: str) -> None:
        _write(tmp_path, path, "-----BEGIN PRIVATE KEY-----\nabc\n")

        assert "Credential files are committed" not in _titles(tmp_path)

    def test_binary_keystores_are_flagged_by_extension(self, tmp_path: Path) -> None:
        """No header to grep, and no public-certificate variant to confuse."""
        _write(tmp_path, "release.jks")

        assert "Credential files are committed" in _titles(tmp_path)


class TestHardcodedSecrets:
    """Gitleaks answers this check now — see test_gitleaks.py for its
    behaviour. What belongs here is that the scanner reports the check at all,
    and reports it honestly when there is no sandbox to run the tool in, which
    is the case for every test in this file."""

    def test_it_is_errored_rather_than_passed_without_a_sandbox(self, tmp_path: Path) -> None:
        """The regex implementation this replaced would have said "passed" —
        which, with nothing having looked, was the one answer that could not be
        justified."""
        _write(tmp_path, "app.py", "x = 1\n")
        results = SCANNER.scan(RepositoryIndex.build(tmp_path, framework="FastAPI"))

        secrets = next(r for r in results if r.id == "security.hardcoded_secrets")

        assert secrets.outcome is CheckOutcome.ERRORED
        assert secrets.reason
        assert secrets.finding is None


class TestToolBackedChecks:
    """All three now have a tool. With no sandbox — which is every test in this
    file — each reports ERRORED rather than passing, because nothing looked."""

    @pytest.mark.parametrize(
        "check_id",
        [
            "security.hardcoded_secrets",
            "security.dependency_vulnerabilities",
            "security.code_patterns",
        ],
    )
    def test_reports_errored_without_a_sandbox(self, tmp_path: Path, check_id: str) -> None:
        _write(tmp_path, "app.py", "x = 1\n")
        results = SCANNER.scan(RepositoryIndex.build(tmp_path, framework="FastAPI"))

        result = next(r for r in results if r.id == check_id)

        assert result.outcome is CheckOutcome.ERRORED
        assert result.finding is None
        assert result.reason


class TestDebugMode:
    def test_flags_unconditional_debug(self, tmp_path: Path) -> None:
        _write(tmp_path, "settings.py", "DEBUG = True\n")

        assert "Debug mode is enabled" in _titles(tmp_path)

    def test_flags_flask_run_debug(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "app.run(debug=True)\n")

        assert "Debug mode is enabled" in _titles(tmp_path)

    def test_reading_the_flag_from_the_environment_is_correct(self, tmp_path: Path) -> None:
        _write(tmp_path, "settings.py", "DEBUG = os.getenv('DEBUG', 'false') == 'true'\n")

        assert "Debug mode is enabled" not in _titles(tmp_path)

    @pytest.mark.parametrize("name", ["settings/dev.py", "local.py", "config.override.py"])
    def test_development_files_are_for_development(self, tmp_path: Path, name: str) -> None:
        _write(tmp_path, name, "DEBUG = True\n")

        assert "Debug mode is enabled" not in _titles(tmp_path)


class TestTlsVerification:
    @pytest.mark.parametrize(
        "line",
        [
            "requests.get(url, verify=False)",
            "const agent = { rejectUnauthorized: false };",
            "tls.Config{InsecureSkipVerify: true}",
            "ctx = ssl._create_unverified_context()",
        ],
    )
    def test_flags_disabled_verification(self, tmp_path: Path, line: str) -> None:
        _write(tmp_path, "client.py", line + "\n")

        assert "TLS certificate verification is disabled" in _titles(tmp_path)

    def test_ordinary_tls_use_is_not_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "client.py", "requests.get(url, timeout=5)\n")

        assert "TLS certificate verification is disabled" not in _titles(tmp_path)


class TestContainerSecrets:
    def test_flags_a_literal_password_in_a_dockerfile(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", "FROM python:3.14\nENV DB_PASSWORD=hunter2hunter2\n")

        assert "Secrets are baked into container configuration" in _titles(tmp_path)

    def test_flags_a_literal_secret_in_compose(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  api:\n    environment:\n      SECRET_KEY: prod-h8kQz94xLm\n",
        )

        assert "Secrets are baked into container configuration" in _titles(tmp_path)

    def test_interpolation_is_the_correct_pattern(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  api:\n    environment:\n      SECRET_KEY: ${SECRET_KEY}\n",
        )

        assert "Secrets are baked into container configuration" not in _titles(tmp_path)

    def test_a_non_secret_env_is_not_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", "FROM python:3.14\nENV APP_PORT=8000 LOG_LEVEL=info\n")

        assert "Secrets are baked into container configuration" not in _titles(tmp_path)

    def test_an_empty_key_does_not_claim_the_next_line(self, tmp_path: Path) -> None:
        """Sentry's compose file: `SES_PASSWORD:` with no value, followed by
        `SES_REGION:`. Whitespace crossing the newline made the next line the
        "value" of the empty one."""
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  api:\n    environment:\n      SES_PASSWORD:\n      SES_REGION: eu-1\n",
        )

        assert "Secrets are baked into container configuration" not in _titles(tmp_path)

    def test_a_short_quoted_default_is_not_a_secret(self, tmp_path: Path) -> None:
        """`AWS_SECRET_KEY: "sentry"` reaches eight characters only if the
        quotes are counted as secret material."""
        _write(
            tmp_path,
            "docker-compose.yml",
            'services:\n  api:\n    environment:\n      AWS_SECRET_KEY: "sentry"\n',
        )

        assert "Secrets are baked into container configuration" not in _titles(tmp_path)


class TestGitignoreProtection:
    def test_flags_env_usage_with_no_ignore_rule(self, tmp_path: Path) -> None:
        _write(tmp_path, "requirements.txt", "python-dotenv\n")
        _write(tmp_path, ".gitignore", "*.pyc\n")

        assert ".gitignore does not protect env files" in _titles(tmp_path)

    def test_a_missing_gitignore_also_counts(self, tmp_path: Path) -> None:
        _write(tmp_path, "requirements.txt", "python-dotenv\n")

        assert ".gitignore does not protect env files" in _titles(tmp_path)

    def test_a_project_not_using_env_files_has_nothing_to_protect(self, tmp_path: Path) -> None:
        _write(tmp_path, "requirements.txt", "fastapi\n")

        assert ".gitignore does not protect env files" not in _titles(tmp_path)

    def test_a_covering_rule_passes(self, tmp_path: Path) -> None:
        _write(tmp_path, "requirements.txt", "python-dotenv\n")
        _write(tmp_path, ".gitignore", ".env\n.env.*\n")

        assert ".gitignore does not protect env files" not in _titles(tmp_path)


class TestFindingQuality:
    def test_findings_name_the_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "deploy/id_rsa", "-----BEGIN PRIVATE KEY-----\n")
        finding = next(f for f in _scan(tmp_path) if f.title.startswith("Credential files"))

        assert "deploy/id_rsa" in finding.description

    def test_one_finding_per_problem_not_per_file(self, tmp_path: Path) -> None:
        for name in ("a/id_rsa", "b/id_rsa", "c/id_rsa"):
            _write(tmp_path, name, "-----BEGIN PRIVATE KEY-----\n")

        credentials = [f for f in _scan(tmp_path) if f.title.startswith("Credential files")]

        assert len(credentials) == 1
        assert "2 other files" in credentials[0].description

    def test_credential_findings_lead_with_rotation(self, tmp_path: Path) -> None:
        """Removing a leaked credential without rotating it fixes nothing; the
        advice must put rotation first."""
        _write(tmp_path, ".env", "DB_PASSWORD=r3al-lo0king-value\n")

        for f in _scan(tmp_path):
            if f.severity is Severity.CRITICAL:
                assert f.recommendation.lower().startswith("rotate")
