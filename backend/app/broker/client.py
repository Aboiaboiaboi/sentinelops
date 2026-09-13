"""The worker's side of the broker connection.

`BrokerSandbox` implements the same `SandboxRunner` Protocol `DockerSandbox`
does (app/utils/sandbox.py), so it drops straight into `get_sandbox()` /
`set_sandbox()` and every existing call site in the scanner tool modules —
none of them know or care whether they are talking to a container's own
docker.sock or to this HTTP-over-Unix-socket client.
"""

import logging
from pathlib import Path

import httpx

from app.broker.protocol import request_from_spec, result_from_response
from app.utils.sandbox import SandboxResult, SandboxSpec, SandboxUnavailable

logger = logging.getLogger(__name__)


class BrokerSandbox:
    """Asks the broker to run a tool, instead of running one directly.

    Holds no Docker access itself — the worker container mounts only the
    broker's own narrow socket (see docker-compose.yml), never
    /var/run/docker.sock. `httpx`'s Unix-socket transport is already a real
    dependency of this project (backend/pyproject.toml), so this needs no new
    package.
    """

    def __init__(self, socket_path: str, *, timeout_seconds: float = 330) -> None:
        self._socket_path = socket_path
        # A little longer than the sandbox's own outer ceiling
        # (sandbox_timeout_seconds, default 300) — the broker's request should
        # always answer before its own tool timeout fires; this is a backstop
        # against the broker itself being unreachable, not against a slow tool.
        self._client = httpx.Client(
            transport=httpx.HTTPTransport(uds=socket_path),
            base_url="http://sentinelops-broker",
            timeout=timeout_seconds,
        )

    def health(self) -> str | None:
        """None if the broker answered; otherwise the reason it didn't —
        mirrors DockerSandbox.verify()'s shape so workers/settings.py can
        treat the two identically."""
        try:
            response = self._client.get("/health")
        except httpx.HTTPError as exc:
            return f"the scan broker is not reachable at {self._socket_path}: {exc}"
        if response.status_code != 200:
            return f"the scan broker at {self._socket_path} answered with {response.status_code}"
        return None

    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        try:
            response = self._client.post("/run", json=request_from_spec(spec, repo_path.as_posix()))
        except httpx.HTTPError as exc:
            raise SandboxUnavailable(
                f"{spec.image} was not run: the scan broker is not reachable at "
                f"{self._socket_path}: {exc}"
            ) from exc

        if response.status_code == 403:
            # Should never happen in practice — the worker only ever asks for
            # one of the five images the broker already allows — but if it
            # ever does, that is a bug worth surfacing loudly, not silencing.
            raise SandboxUnavailable(
                f"{spec.image} was refused by the scan broker: {response.text}"
            )
        if response.status_code == 503:
            raise SandboxUnavailable(
                response.json().get("error", "the scan broker reported itself unavailable")
            )
        if response.status_code != 200:
            raise SandboxUnavailable(
                f"{spec.image} was not run: the scan broker answered with "
                f"{response.status_code}: {response.text}"
            )

        return result_from_response(response.json())
