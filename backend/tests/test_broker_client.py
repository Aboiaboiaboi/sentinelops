"""BrokerSandbox — the worker's side of the connection to the scan broker.

Tested against httpx.MockTransport rather than a real Unix socket: this
project's own broker (app/broker/server.py) only runs on Linux, and this
suite also runs on Windows, where socket.AF_UNIX does not exist. What's
exercised here — request/response translation and error handling — does not
depend on the transport being real.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.broker.client import BrokerSandbox
from app.utils.sandbox import SandboxResult, SandboxRunner, SandboxSpec, SandboxUnavailable

SPEC = SandboxSpec(
    image="ghcr.io/gitleaks/gitleaks:v8.30.1",
    command=("detect", "--source", "{repo}"),
    timeout_seconds=60,
)


def _client_with(handler) -> BrokerSandbox:
    sandbox = BrokerSandbox("/fake-broker.sock")
    sandbox._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://sentinelops-broker"
    )
    return sandbox


def test_broker_sandbox_satisfies_the_protocol() -> None:
    assert isinstance(BrokerSandbox("/fake-broker.sock"), SandboxRunner)


def test_health_returns_none_on_a_200() -> None:
    sandbox = _client_with(lambda request: httpx.Response(200, json={"status": "ok"}))

    assert sandbox.health() is None


def test_health_names_a_non_200_status() -> None:
    sandbox = _client_with(lambda request: httpx.Response(503))

    reason = sandbox.health()

    assert reason is not None
    assert "503" in reason


def test_health_reports_a_connection_failure() -> None:
    def raise_connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no such file or directory", request=request)

    sandbox = _client_with(raise_connect_error)

    reason = sandbox.health()

    assert reason is not None
    assert "not reachable" in reason
    assert "/fake-broker.sock" in reason


def test_run_posts_the_spec_and_returns_a_result(tmp_path) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "exit_code": 0,
                "stdout": "clean",
                "stderr": "",
                "timed_out": False,
                "repo_mount": "/data",
            },
        )

    sandbox = _client_with(handler)

    result = sandbox.run(SPEC, repo_path=tmp_path / "scan-1")

    assert seen["body"]["image"] == SPEC.image
    assert seen["body"]["command"] == list(SPEC.command)
    assert isinstance(result, SandboxResult)
    assert result.stdout == "clean"


def test_run_raises_sandbox_unavailable_on_a_refused_image() -> None:
    sandbox = _client_with(
        lambda request: httpx.Response(403, json={"error": "image not allowed: evil:1"})
    )

    with pytest.raises(SandboxUnavailable, match="refused"):
        sandbox.run(SPEC, repo_path=Path("/data/repos/scan-1"))


def test_run_raises_sandbox_unavailable_when_the_broker_is_unreachable() -> None:
    def raise_connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no such file or directory", request=request)

    sandbox = _client_with(raise_connect_error)

    with pytest.raises(SandboxUnavailable, match="not reachable"):
        sandbox.run(SPEC, repo_path=Path("/data/repos/scan-1"))
