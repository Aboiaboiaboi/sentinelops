"""docker-compose.yml has to agree with what the worker expects.

Three facts here are load-bearing and none of them fail loudly on their own:

- A `--mount source=` naming a volume that does not exist **creates an empty
  one** rather than refusing, so a name that disagrees with compose means every
  tool scans an empty directory and reports a clean repository.
- Compose prefixes volume names with the project, which defaults to the checkout
  directory's name — so a clone into `sentinelops-fork/` renames the volume
  unless it is declared explicitly.
- An unpinned image can change under a scan, which makes a score change
  unexplainable. `SandboxSpec` refuses one; nothing stopped compose from
  shipping one until this file.

Parsed with regular expressions rather than a YAML library, the same way
test_env_example.py reads .env.example — deliberately dumb, and the alternative
is a dependency carried by the whole project for one test.
"""

import re
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _referenced_volumes() -> dict[str, str]:
    """Volume names the worker is told to mount, by environment variable."""
    return {
        match.group(1): match.group(2)
        for match in re.finditer(r"^\s+(SANDBOX\w*_VOLUME):\s*(\S+)\s*$", _text(), re.MULTILINE)
    }


def _declared_volume_names() -> set[str]:
    """Names from the top-level `volumes:` block, which is the only place a
    `name:` two spaces deep can appear."""
    _, _, volumes_block = _text().partition("\nvolumes:\n")
    return set(re.findall(r"^ {4}name:\s*(\S+)\s*$", volumes_block, re.MULTILINE))


def test_the_compose_file_exists() -> None:
    assert COMPOSE.is_file()


def test_both_sandbox_volumes_are_named() -> None:
    """A spot check that the variables exist at all, so the test below cannot
    pass by finding nothing to check."""
    assert set(_referenced_volumes()) == {"SANDBOX_VOLUME", "SANDBOX_CACHE_VOLUME"}


def test_every_referenced_volume_is_declared_explicitly() -> None:
    declared = _declared_volume_names()

    for variable, volume in _referenced_volumes().items():
        assert volume in declared, (
            f"{variable} is set to {volume!r}, which no volume declares with an explicit "
            f"name:. Docker would create an empty volume by that name instead of failing, "
            f"and every tool would scan nothing. Declared: {sorted(declared)}"
        )


@pytest.mark.parametrize("variable", ["SANDBOX_VOLUME", "SANDBOX_CACHE_VOLUME"])
def test_a_referenced_volume_carries_the_project_prefix(variable: str) -> None:
    """The name the host daemon knows, not the name compose knows it by."""
    assert _referenced_volumes()[variable].startswith("sentinelops_")


def test_the_worker_bounds_how_many_containers_it_may_run() -> None:
    """Without this the ceiling is arq's max_jobs times the tools a scanner runs
    at once — a product of two numbers in two files that neither one states, and
    at 512 MB a container it exceeds a default Docker VM."""
    match = re.search(r"^\s+SANDBOX_MAX_CONCURRENT:\s*\"?(\d+)\"?\s*$", _text(), re.MULTILINE)

    assert match, "the worker does not bound its concurrent containers"
    assert int(match.group(1)) >= 1


def test_the_declared_ceiling_fits_a_developer_machine() -> None:
    """The number worth knowing is the product, and nothing computes it until
    something is killed for exceeding it. 4 GB is a Docker Desktop default."""
    from app.config import get_settings

    settings = get_settings()
    peak_mb = settings.sandbox_max_concurrent * settings.sandbox_memory_mb

    assert peak_mb <= 4096, f"a saturated worker would want {peak_mb} MB of containers"


def test_every_image_is_pinned() -> None:
    """Same rule SandboxSpec enforces in code, applied to the tool images that
    are launched by compose rather than by the sandbox."""
    for image in re.findall(r"^\s+image:\s*(\S+)\s*$", _text(), re.MULTILINE):
        assert ":" in image, f"{image} has no tag"
        assert not image.endswith(":latest"), f"{image} is not pinned"


def test_the_docker_socket_is_mounted_into_the_worker_and_nothing_else() -> None:
    """The socket is root on the host. Anything that can reach it can start a
    privileged container, so exactly one service may have it — and the API,
    which cannot even clone a repository, is not that service."""
    services, _, _ = _text().partition("\nvolumes:\n")
    before_worker, separator, _after = services.partition("\n  worker:\n")

    assert separator, "the worker service was renamed; this test needs updating"
    assert "/var/run/docker.sock" not in before_worker
    # Counted by line: the mount itself names the socket twice, source and
    # target, and this is asking how many places mount it.
    mounting_lines = [line for line in _text().splitlines() if "/var/run/docker.sock" in line]
    assert len(mounting_lines) == 1
