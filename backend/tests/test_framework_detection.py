"""Tests for framework detection.

Detection is manifest-based on purpose — a wrong guess is worse than no guess,
because later scanners change what they look for based on the answer.
"""

import json
from pathlib import Path

import pytest

from app.scanners.framework import detect_framework


def _write(root: Path, name: str, content: str) -> None:
    (root / name).write_text(content, encoding="utf-8")


class TestPython:
    @pytest.mark.parametrize(
        ("dependency", "expected"),
        [
            ("fastapi", "FastAPI"),
            ("Django", "Django"),
            ("flask", "Flask"),
        ],
    )
    def test_detects_the_framework_from_requirements(
        self, tmp_path: Path, dependency: str, expected: str
    ) -> None:
        _write(tmp_path, "requirements.txt", f"{dependency}==1.0\nuvicorn\n")

        assert detect_framework(tmp_path) == expected

    def test_detects_from_pyproject(self, tmp_path: Path) -> None:
        _write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["fastapi>=0.115"]\n')

        assert detect_framework(tmp_path) == "FastAPI"

    def test_falls_back_to_the_language(self, tmp_path: Path) -> None:
        """A Python project with no recognised framework is still Python."""
        _write(tmp_path, "requirements.txt", "requests\nnumpy\n")

        assert detect_framework(tmp_path) == "Python"


class TestNode:
    def test_detects_from_declared_dependencies(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"dependencies": {"express": "^4"}}))

        assert detect_framework(tmp_path) == "Express"

    def test_detects_a_dev_dependency(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"devDependencies": {"svelte": "^4"}}))

        assert detect_framework(tmp_path) == "Svelte"

    def test_ignores_a_framework_named_only_in_a_script(self, tmp_path: Path) -> None:
        """Otherwise `"build": "next build"` in an Express app reports Next.js."""
        _write(
            tmp_path,
            "package.json",
            json.dumps({"scripts": {"build": "next build"}, "dependencies": {"express": "^4"}}),
        )

        assert detect_framework(tmp_path) == "Express"

    def test_malformed_manifest_still_identifies_node(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", "{ not valid json")

        assert detect_framework(tmp_path) == "Node.js"

    def test_falls_back_to_the_runtime(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"dependencies": {"lodash": "^4"}}))

        assert detect_framework(tmp_path) == "Node.js"


class TestOtherStacks:
    @pytest.mark.parametrize(
        ("filename", "content", "expected"),
        [
            ("go.mod", "module example.com/x\n", "Go"),
            ("Cargo.toml", '[package]\nname = "x"\n', "Rust"),
            (
                "pom.xml",
                "<project><artifactId>spring-boot-starter</artifactId></project>",
                "Spring Boot",
            ),
            ("pom.xml", "<project><artifactId>plain</artifactId></project>", "Java"),
            ("Gemfile", "gem 'rails'\n", "Ruby on Rails"),
            ("Gemfile", "gem 'nokogiri'\n", "Ruby"),
            ("composer.json", '{"require": {"laravel/framework": "^11"}}', "Laravel"),
            ("composer.json", '{"require": {"monolog/monolog": "^3"}}', "PHP"),
        ],
    )
    def test_detects(self, tmp_path: Path, filename: str, content: str, expected: str) -> None:
        _write(tmp_path, filename, content)

        assert detect_framework(tmp_path) == expected


class TestNoMatch:
    def test_empty_repository_is_none(self, tmp_path: Path) -> None:
        """None is more useful than a confidently wrong guess."""
        assert detect_framework(tmp_path) is None

    def test_documentation_only_repository_is_none(self, tmp_path: Path) -> None:
        _write(tmp_path, "README.md", "# docs\n")

        assert detect_framework(tmp_path) is None

    def test_does_not_descend_into_subdirectories(self, tmp_path: Path) -> None:
        """A manifest in an example directory or a vendored dependency must not
        be mistaken for the project's own."""
        nested = tmp_path / "examples" / "demo"
        nested.mkdir(parents=True)
        _write(nested, "package.json", json.dumps({"dependencies": {"react": "^18"}}))

        assert detect_framework(tmp_path) is None


class TestPolyglot:
    def test_backend_wins_over_a_bundled_frontend(self, tmp_path: Path) -> None:
        """A Django service with a small React admin is a Django service."""
        _write(tmp_path, "requirements.txt", "django==5.0\n")
        _write(tmp_path, "package.json", json.dumps({"dependencies": {"react": "^18"}}))

        assert detect_framework(tmp_path) == "Django"
