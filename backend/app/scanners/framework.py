"""Framework detection.

Answers "what is this repository built with", which fills in `Project.framework`
— null until a scan runs. Later scanners use it too: whether a missing Dockerfile
matters, or what a healthcheck should look like, depends on the stack.

Detection is manifest-based and deliberately shallow. Reading declared
dependencies is fast and reliable; inferring a framework from source code is
neither, and a wrong guess is worse than no guess because it would silently
change what other scanners look for.
"""

import json
from collections.abc import Callable
from pathlib import Path

from app.scanners.base import read_text

# Manifest -> (dependency substring, framework name), most specific first. The
# substrings are matched against the raw manifest text, which is enough to tell
# `django` from `fastapi` without parsing five different dependency formats.
_PYTHON_FRAMEWORKS = (
    ("fastapi", "FastAPI"),
    ("django", "Django"),
    ("flask", "Flask"),
    ("pyramid", "Pyramid"),
    ("tornado", "Tornado"),
)

_NODE_FRAMEWORKS = (
    ("next", "Next.js"),
    ("nuxt", "Nuxt"),
    ("@nestjs/core", "NestJS"),
    ("@angular/core", "Angular"),
    ("svelte", "Svelte"),
    ("express", "Express"),
    ("react", "React"),
    ("vue", "Vue"),
)

_JVM_FRAMEWORKS = (
    ("spring-boot", "Spring Boot"),
    ("quarkus", "Quarkus"),
    ("micronaut", "Micronaut"),
)

_RUBY_FRAMEWORKS = (("rails", "Ruby on Rails"), ("sinatra", "Sinatra"))

_PHP_FRAMEWORKS = (("laravel/framework", "Laravel"), ("symfony/", "Symfony"))


def _first_match(haystack: str, candidates: tuple[tuple[str, str], ...]) -> str | None:
    lowered = haystack.lower()
    for needle, name in candidates:
        if needle in lowered:
            return name
    return None


def _detect_python(repo_path: Path) -> str | None:
    manifests = ["pyproject.toml", "requirements.txt", "Pipfile", "setup.py", "setup.cfg"]
    combined = "".join(read_text(repo_path / name) for name in manifests)
    if not combined:
        return None
    return _first_match(combined, _PYTHON_FRAMEWORKS) or "Python"


def _detect_node(repo_path: Path) -> str | None:
    raw = read_text(repo_path / "package.json")
    if not raw:
        return None

    # Only declared dependencies, so a framework merely mentioned in a script
    # name or description does not count.
    try:
        manifest = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # A malformed package.json still tells us it is a Node project.
        return "Node.js"

    declared = " ".join(
        key
        for field in ("dependencies", "devDependencies", "peerDependencies")
        for key in (manifest.get(field) or {})
        if isinstance(manifest.get(field), dict)
    )
    return _first_match(declared, _NODE_FRAMEWORKS) or "Node.js"


def _detect_go(repo_path: Path) -> str | None:
    return "Go" if read_text(repo_path / "go.mod") else None


def _detect_rust(repo_path: Path) -> str | None:
    return "Rust" if read_text(repo_path / "Cargo.toml") else None


def _detect_jvm(repo_path: Path) -> str | None:
    combined = "".join(
        read_text(repo_path / name) for name in ("pom.xml", "build.gradle", "build.gradle.kts")
    )
    if not combined:
        return None
    return _first_match(combined, _JVM_FRAMEWORKS) or "Java"


def _detect_ruby(repo_path: Path) -> str | None:
    combined = read_text(repo_path / "Gemfile") + read_text(repo_path / "Gemfile.lock")
    if not combined:
        return None
    return _first_match(combined, _RUBY_FRAMEWORKS) or "Ruby"


def _detect_php(repo_path: Path) -> str | None:
    raw = read_text(repo_path / "composer.json")
    if not raw:
        return None
    return _first_match(raw, _PHP_FRAMEWORKS) or "PHP"


def _detect_dotnet(repo_path: Path) -> str | None:
    for path in repo_path.glob("*.csproj"):
        if path.is_file():
            return ".NET"
    return ".NET" if read_text(repo_path / "global.json") else None


# Order matters only for polyglot repositories, where the first match wins.
# Backend stacks come before frontend ones: a Django service with a small React
# admin is a Django service.
_DETECTORS: tuple[Callable[[Path], str | None], ...] = (
    _detect_python,
    _detect_jvm,
    _detect_go,
    _detect_rust,
    _detect_ruby,
    _detect_php,
    _detect_dotnet,
    _detect_node,
)


# Language names, as opposed to framework names. Returned when a manifest exists
# but names nothing recognisable, and the signal that it is worth looking one
# level down before settling for it.
_LANGUAGE_ONLY = frozenset({"Python", "Node.js", "Go", "Rust", "Java", "Ruby", "PHP", ".NET"})

# Where a service's manifest lives when the repository root belongs to the
# workspace rather than to any one component. Named directories only, one level
# deep: descending freely would find a manifest in an example or a fixture and
# report that instead.
_COMPONENT_DIRECTORIES = ("backend", "server", "api", "service", "app", "src")


def _detect_at(path: Path) -> str | None:
    for detect in _DETECTORS:
        framework = detect(path)
        if framework is not None:
            return framework
    return None


def detect_framework(repo_path: Path) -> str | None:
    """Name the stack, or None if nothing recognisable can be found.

    The root is examined first and wins whenever it names an actual framework.
    Only when the root says nothing more specific than a language does this look
    one level down, into conventionally named component directories.

    That second pass exists because a monorepo — `backend/` beside `frontend/` —
    is the ordinary shape for the kind of application this assesses, and its
    root manifest is usually workspace configuration naming no framework at all.
    On tiangolo/full-stack-fastapi-template the root reported "Python" while the
    FastAPI dependency sat in backend/pyproject.toml, so `is_service` was False
    and every service-only check across three categories quietly skipped.
    """
    root = _detect_at(repo_path)
    if root is not None and root not in _LANGUAGE_ONLY:
        return root

    for name in _COMPONENT_DIRECTORIES:
        candidate = repo_path / name
        # Symlinks are not followed: a repository is untrusted input and may
        # link anywhere on the filesystem.
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        nested = _detect_at(candidate)
        if nested is not None and nested not in _LANGUAGE_ONLY:
            return nested

    return root
