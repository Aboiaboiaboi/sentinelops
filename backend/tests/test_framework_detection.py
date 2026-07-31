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


class TestMonorepoLayout:
    """A workspace root beside `backend/` and `frontend/` is the ordinary shape
    for the applications this tool assesses, and its root manifest usually names
    no framework at all. Found by scanning tiangolo/full-stack-fastapi-template,
    which reported "Python" while FastAPI sat in backend/pyproject.toml —
    leaving `is_service` False and silently skipping every service-only check in
    reliability, observability and scalability.
    """

    def test_finds_a_framework_in_a_component_directory(self, tmp_path: Path) -> None:
        _write(tmp_path, "pyproject.toml", "[tool.copier]\nname = 'template'\n")
        backend = tmp_path / "backend"
        backend.mkdir()
        _write(backend, "pyproject.toml", "dependencies = ['fastapi']\n")

        assert detect_framework(tmp_path) == "FastAPI"

    @pytest.mark.parametrize("directory", ["backend", "server", "api", "service", "app", "src"])
    def test_recognises_the_conventional_names(self, tmp_path: Path, directory: str) -> None:
        component = tmp_path / directory
        component.mkdir()
        _write(component, "requirements.txt", "django==5.0\n")

        assert detect_framework(tmp_path) == "Django"

    def test_the_root_still_wins_when_it_names_a_framework(self, tmp_path: Path) -> None:
        """A real root manifest is the better answer; the nested pass is a
        fallback, not a competitor."""
        _write(tmp_path, "requirements.txt", "flask==3.0\n")
        backend = tmp_path / "backend"
        backend.mkdir()
        _write(backend, "requirements.txt", "django==5.0\n")

        assert detect_framework(tmp_path) == "Flask"

    def test_a_language_only_root_is_kept_when_nothing_nested_is_better(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "pyproject.toml", "[project]\nname = 'tool'\n")

        assert detect_framework(tmp_path) == "Python"

    def test_still_does_not_descend_into_unconventional_directories(self, tmp_path: Path) -> None:
        """The fallback is a fixed list of names, not a search."""
        _write(tmp_path, "pyproject.toml", "[project]\nname = 'tool'\n")
        examples = tmp_path / "examples"
        examples.mkdir()
        _write(examples, "requirements.txt", "fastapi\n")

        assert detect_framework(tmp_path) == "Python"

    def test_component_symlinks_are_not_followed(self, tmp_path: Path) -> None:
        """A repository is untrusted input and can link anywhere."""
        outside = tmp_path / "outside"
        outside.mkdir()
        _write(outside, "requirements.txt", "fastapi\n")
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "pyproject.toml", "[project]\nname = 'tool'\n")
        try:
            (repo / "backend").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation requires privileges on this platform")

        assert detect_framework(repo) == "Python"
