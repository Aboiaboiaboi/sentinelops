"""The wire format between the worker and the broker, and the allowlist.

Deliberately separate from server.py: this module is pure data — no socket, no
subprocess — so the allowlist and request/response shapes are testable on any
platform, including the Windows dev machine this was written on, where
`socket.AF_UNIX` does not exist. server.py is the only file that touches an
actual socket.
"""

from dataclasses import asdict

from app.scanners.deployment.tools.checkov import IMAGE as CHECKOV_IMAGE
from app.scanners.deployment.tools.hadolint import IMAGE as HADOLINT_IMAGE
from app.scanners.security.tools.gitleaks import IMAGE as GITLEAKS_IMAGE
from app.scanners.security.tools.semgrep import IMAGE as SEMGREP_IMAGE
from app.scanners.security.tools.trivy import IMAGE as TRIVY_IMAGE
from app.utils.sandbox import SandboxResult, SandboxSpec

# The only images this process will ever run, regardless of what a request
# asks for. Imported from the tool modules themselves rather than retyped —
# deploy.sh's own comment already laments needing to keep the pre-pull list in
# sync by hand with these; a broker-side copy would be a third place to drift.
ALLOWED_IMAGES = frozenset(
    {GITLEAKS_IMAGE, TRIVY_IMAGE, SEMGREP_IMAGE, HADOLINT_IMAGE, CHECKOV_IMAGE}
)


class ImageNotAllowed(Exception):
    """A request asked for an image outside ALLOWED_IMAGES.

    Raised before a SandboxSpec is even constructed — the allowlist is the
    first gate, not a property checked afterward.
    """


def spec_from_request(body: dict) -> tuple[SandboxSpec, str]:
    """A validated (SandboxSpec, repo_path) from a decoded JSON request body.

    Raises ImageNotAllowed for anything outside the fixed five, and whatever
    KeyError/TypeError/ValueError a malformed body produces otherwise — the
    caller (server.py) turns both into the right HTTP status, never a crash.

    `repo_path` comes back as a plain string, not a Path: it is never used to
    touch this process's own filesystem. In the named-volume case (the only
    one this broker is configured for — see DockerSandbox._mount_arguments)
    it is purely a string substituted into each tool's argv for the path
    *inside* the tool container. The volume actually mounted is fixed by this
    broker's own startup configuration and cannot be changed by a request, so
    a hostile repo_path can at worst point a tool at the wrong sub-path within
    that one volume — not escape it.
    """
    image = str(body["image"])
    if image not in ALLOWED_IMAGES:
        raise ImageNotAllowed(image)
    spec = SandboxSpec(
        image=image,
        command=tuple(str(argument) for argument in body["command"]),
        timeout_seconds=int(body["timeout_seconds"]),
        memory_mb=int(body.get("memory_mb", 512)),
        needs_cache=bool(body.get("needs_cache", False)),
        environment=tuple((str(k), str(v)) for k, v in body.get("environment", [])),
    )
    return spec, str(body["repo_path"])


def request_from_spec(spec: SandboxSpec, repo_path: str) -> dict:
    """The client's half of the same shape — kept beside spec_from_request so
    the two can never drift apart from each other."""
    return {
        "image": spec.image,
        "command": list(spec.command),
        "timeout_seconds": spec.timeout_seconds,
        "memory_mb": spec.memory_mb,
        "needs_cache": spec.needs_cache,
        "environment": [list(pair) for pair in spec.environment],
        "repo_path": repo_path,
    }


def response_from_result(result: SandboxResult) -> dict:
    return asdict(result)


def result_from_response(body: dict) -> SandboxResult:
    return SandboxResult(
        exit_code=int(body["exit_code"]),
        stdout=str(body["stdout"]),
        stderr=str(body["stderr"]),
        timed_out=bool(body["timed_out"]),
        repo_mount=str(body.get("repo_mount", "")),
    )
