"""docker-compose.yml and the scan broker's systemd unit have to agree with
each other, and with what DockerSandbox expects.

Four facts here are load-bearing and none of them fail loudly on their own:

- A `--mount source=` naming a volume that does not exist **creates an empty
  one** rather than refusing, so a name that disagrees between the compose
  file (which declares the volumes) and the broker unit (which mounts them by
  name) means every tool scans an empty directory and reports a clean
  repository.
- Compose prefixes volume names with the project, which defaults to the checkout
  directory's name — so a clone into `sentinelops-fork/` renames the volume
  unless it is declared explicitly.
- An unpinned image can change under a scan, which makes a score change
  unexplainable. `SandboxSpec` refuses one; nothing stopped compose from
  shipping one until this file.
- The worker must never mount the real Docker socket again — that line is
  what the deployment.privileged check exists to catch, and the whole point
  of the scan broker (backend/app/broker/) is that no committed file needs it.

Parsed with regular expressions rather than a YAML library, the same way
test_env_example.py reads .env.example — deliberately dumb, and the alternative
is a dependency carried by the whole project for one test.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
BROKER_UNIT = REPO_ROOT / "deploy" / "compose" / "sentinelops-broker.service"
PROVISION_DEV = REPO_ROOT / "deploy" / "compose" / "provision-dev.sh"


def _text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Comment lines stripped, so a line documenting the socket path in prose
    is not mistaken for a line that actually mounts it — the same reasoning
    app.scanners.deployment.parsing.code_only applies to the check itself."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def _unit_env(variable: str) -> str | None:
    """The *placeholder* an environment variable holds in the committed
    template — provision.sh/provision-dev.sh substitute the real value in at
    install time, so this file never contains one."""
    match = re.search(
        rf"^Environment={variable}=(\S+)\s*$", BROKER_UNIT.read_text(encoding="utf-8"), re.MULTILINE
    )
    return match.group(1) if match else None


def _dev_substitution(placeholder: str) -> str | None:
    """The real value provision-dev.sh's sed pipeline substitutes for a given
    __PLACEHOLDER__ before installing the unit — this is where the actual
    dev volume names live, not in the template itself."""
    text = PROVISION_DEV.read_text(encoding="utf-8")
    match = re.search(rf"__{placeholder}__#([^#]+)#", text)
    return match.group(1) if match else None


def _declared_volume_names() -> set[str]:
    """Names from the top-level `volumes:` block, which is the only place a
    `name:` two spaces deep can appear."""
    _, _, volumes_block = _text().partition("\nvolumes:\n")
    return set(re.findall(r"^ {4}name:\s*(\S+)\s*$", volumes_block, re.MULTILINE))


def test_the_compose_file_exists() -> None:
    assert COMPOSE.is_file()


def test_the_broker_unit_exists() -> None:
    assert BROKER_UNIT.is_file()


def test_the_unit_template_declares_placeholders_not_real_values() -> None:
    """A spot check that the variables exist at all in the template, so the
    substitution test below cannot pass by finding nothing to check."""
    assert _unit_env("SANDBOX_VOLUME") == "__SANDBOX_VOLUME__"
    assert _unit_env("SANDBOX_CACHE_VOLUME") == "__SANDBOX_CACHE_VOLUME__"


def test_provision_dev_substitutes_volumes_compose_declares() -> None:
    """provision-dev.sh's sed pipeline is where the real dev volume names
    live — the committed unit file only ever holds placeholders. A name here
    that disagrees with docker-compose.yml's declared volumes means Docker
    creates an empty one instead of failing, and every tool scans nothing."""
    declared = _declared_volume_names()

    for placeholder in ("SANDBOX_VOLUME", "SANDBOX_CACHE_VOLUME"):
        volume = _dev_substitution(placeholder)
        assert volume, f"provision-dev.sh does not substitute __{placeholder}__"
        assert volume in declared, (
            f"__{placeholder}__ is substituted with {volume!r}, which no volume in "
            f"docker-compose.yml declares with an explicit name:. Declared: {sorted(declared)}"
        )


@pytest.mark.parametrize("placeholder", ["SANDBOX_VOLUME", "SANDBOX_CACHE_VOLUME"])
def test_a_dev_substituted_volume_carries_the_project_prefix(placeholder: str) -> None:
    """The name the host daemon knows, not the name compose knows it by."""
    assert (_dev_substitution(placeholder) or "").startswith("sentinelops_")


def test_the_broker_bounds_how_many_containers_it_may_run() -> None:
    """Without this the ceiling is arq's max_jobs times the tools a scanner runs
    at once — a product of two numbers in two files that neither one states, and
    at 512 MB a container it exceeds a default developer machine."""
    concurrent = _unit_env("SANDBOX_MAX_CONCURRENT")

    assert concurrent, "the broker does not bound its concurrent containers"
    assert int(concurrent) >= 1


def test_the_declared_ceiling_fits_a_developer_machine() -> None:
    """The number worth knowing is the product, and nothing computes it until
    something is killed for exceeding it. 4 GB is a modest WSL2 default."""
    concurrent = int(_unit_env("SANDBOX_MAX_CONCURRENT") or "0")
    memory_mb = int(_unit_env("SANDBOX_MEMORY_MB") or "0")
    peak_mb = concurrent * memory_mb

    assert peak_mb <= 4096, f"a saturated broker would want {peak_mb} MB of containers"


def test_every_image_is_pinned() -> None:
    """Same rule SandboxSpec enforces in code, applied to the tool images that
    are launched by compose rather than by the sandbox."""
    for image in re.findall(r"^\s+image:\s*(\S+)\s*$", _text(), re.MULTILINE):
        assert ":" in image, f"{image} has no tag"
        assert not image.endswith(":latest"), f"{image} is not pinned"


def test_the_docker_socket_is_never_mounted_in_compose() -> None:
    """The socket is root on the host. The scan broker (backend/app/broker/,
    a systemd unit, never a compose service) is the only thing that may mount
    it — if this file's *code* ever does again (comments documenting the old
    trade are fine — see code_only in the check this mirrors), the
    deployment.privileged check, and the whole reason the broker exists, is
    being quietly undone."""
    assert "/var/run/docker.sock" not in _code_only(_text())


def test_the_worker_mounts_the_brokers_socket_instead() -> None:
    services, _, _ = _text().partition("\nvolumes:\n")
    before_worker, separator, after_worker_start = services.partition("\n  worker:\n")

    assert separator, "the worker service was renamed; this test needs updating"
    assert "sentinelops-broker.sock" not in before_worker
    assert "sentinelops-broker.sock" in after_worker_start
