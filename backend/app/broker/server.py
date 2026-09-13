"""The broker's HTTP server, listening on a Unix domain socket.

Run as `python -m app.broker.server` from a systemd unit — see
deploy/compose/sentinelops-broker.service. Never started by Docker Compose;
see the package docstring for why that distinction is load-bearing.

Unix domain sockets do not exist on Windows Python builds (this module was
written on one — `socket.AF_UNIX` is simply absent). The platform guards below
keep this module importable everywhere, including this project's own test
suite, while `serve()` itself only actually works on Linux — which is the only
place this process is ever meant to run.
"""

import json
import logging
import os
import socket
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.broker.protocol import (
    ALLOWED_IMAGES,
    ImageNotAllowed,
    response_from_result,
    spec_from_request,
)
from app.utils.sandbox import DockerSandbox, SandboxRunner, SandboxUnavailable

logger = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = "/run/sentinelops-broker.sock"


def sandbox_from_environment() -> DockerSandbox:
    """The broker's own DockerSandbox, configured from its own environment —
    never from anything a caller sends. This is what makes the allowlist
    real: the volume, cache, and concurrency ceiling are fixed once at process
    startup, not negotiable per request.
    """
    return DockerSandbox(
        volume=os.environ.get("SANDBOX_VOLUME", ""),
        cache_volume=os.environ.get("SANDBOX_CACHE_VOLUME", ""),
        max_timeout_seconds=int(os.environ.get("SANDBOX_TIMEOUT_SECONDS", "300")),
        max_memory_mb=int(os.environ.get("SANDBOX_MEMORY_MB", "512")),
        max_concurrent=int(os.environ.get("SANDBOX_MAX_CONCURRENT", "6")),
    )


def make_handler(sandbox: SandboxRunner) -> type[BaseHTTPRequestHandler]:
    """A request handler bound to one sandbox instance, via closure rather
    than a class attribute set after the fact — so nothing can serve a
    request before a sandbox exists."""

    class Handler(BaseHTTPRequestHandler):
        def _write_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            if self.path == "/health":
                self._write_json(200, {"status": "ok", "images": sorted(ALLOWED_IMAGES)})
                return
            self._write_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own naming
            if self.path != "/run":
                self._write_json(404, {"error": "not found"})
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
                spec, repo_path = spec_from_request(body)
            except ImageNotAllowed as exc:
                logger.warning("broker refused a non-allowlisted image", extra={"image": str(exc)})
                self._write_json(403, {"error": f"image not allowed: {exc}"})
                return
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._write_json(400, {"error": f"bad request: {exc}"})
                return

            try:
                result = sandbox.run(spec, repo_path=Path(repo_path))
            except SandboxUnavailable as exc:
                self._write_json(503, {"error": str(exc)})
                return

            self._write_json(200, response_from_result(result))

        def log_message(self, fmt: str, *args: object) -> None:
            # "message" is a reserved LogRecord attribute — passing it in extra
            # raises KeyError at log time (found by actually running this
            # server, not just importing it: every request silently became a
            # broken pipe, since send_response()'s own logging call is what
            # crashed before a single byte reached the client).
            logger.info("broker request", extra={"detail": fmt % args})

    return Handler


# Absent on non-Unix platforms (notably Windows) — guarded here so the module
# stays importable everywhere; only serve() requires it to actually be real.
_AF_UNIX = getattr(socket, "AF_UNIX", None)


class UnixHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """An HTTP server over a Unix socket instead of TCP.

    ThreadingMixIn so one slow tool run doesn't queue every other request
    behind its whole timeout — DockerSandbox's own semaphore (_slots) is still
    what actually bounds how many containers run at once; this only affects
    how quickly a *rejected* or already-queued request gets its answer.
    """

    daemon_threads = True
    allow_reuse_address = False
    if _AF_UNIX is not None:
        address_family = _AF_UNIX

    def server_bind(self) -> None:
        if _AF_UNIX is None:
            raise RuntimeError(
                "Unix domain sockets are not available on this platform — "
                "the broker only runs on Linux (systemd, not Docker Compose)."
            )
        super().server_bind()


def serve(socket_path: str = DEFAULT_SOCKET_PATH, *, group: str = "") -> None:
    path = Path(socket_path)
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    sandbox = sandbox_from_environment()
    # A missing cache is a warning, not a refusal — Gitleaks, Hadolint and
    # Checkov need no cache and run regardless; only Trivy and Semgrep would
    # report errored while the warm services are still populating it.
    cache = os.environ.get("SANDBOX_CACHE_VOLUME", "")
    if cache and not sandbox.volume_exists(cache):
        logger.warning(
            "sandbox cache volume is missing; tools that need it will report errored",
            extra={"volume": cache},
        )

    server = UnixHTTPServer(socket_path, make_handler(sandbox), bind_and_activate=False)
    server.server_bind()
    # Group-owned, not world-reachable — unlike the real Docker socket this
    # replaces. The worker's uid needs to be a member of `group` (see
    # docker-compose.yml's BROKER_GID) to open this at all.
    os.chmod(socket_path, 0o660)
    if group:
        import grp

        os.chown(socket_path, -1, grp.getgrnam(group).gr_gid)
    server.server_activate()

    logger.info("broker listening", extra={"socket": socket_path, "images": sorted(ALLOWED_IMAGES)})
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve(
        os.environ.get("SANDBOX_BROKER_SOCKET", DEFAULT_SOCKET_PATH),
        group=os.environ.get("SENTINELOPS_BROKER_GROUP", "sentinelops-broker"),
    )
