"""arq worker configuration.

Started with `arq app.workers.settings.WorkerSettings`. Kept separate from the
task definitions so importing a task does not drag in worker configuration —
the API imports task *names* as strings and never this module.
"""

import asyncio
import logging
from typing import Any

from arq.connections import RedisSettings

from app.config import get_settings
from app.logging import configure_logging
from app.utils.sandbox import DockerSandbox, NullSandbox, set_sandbox
from app.workers.scan_tasks import run_scan

logger = logging.getLogger(__name__)

settings = get_settings()


async def on_startup(ctx: dict[str, Any]) -> None:
    """Reassert JSON logging, and install the sandbox.

    Logging is redundant when started through `app.workers.main`, which
    configures it before the worker exists. Kept because it is the only thing
    that helps if someone runs the arq CLI directly, where arq's own dictConfig
    would otherwise leave the output as plain text.

    The sandbox is installed here and nowhere else. The API never runs a tool —
    it does not even have git — so a sandbox in the API process would be one
    more thing able to reach the Docker socket for no reason.
    """
    configure_logging(settings.log_level)

    if not settings.sandbox_enabled:
        # NullSandbox raises rather than running anything; a check with no
        # sandbox reports errored, never passes. Given the reason explicitly so
        # the errored check says "SANDBOX_ENABLED is not set" rather than
        # something generic.
        logger.info("sandbox disabled; tool checks will report errored")
        set_sandbox(NullSandbox("SANDBOX_ENABLED is not set on this worker"))
        return

    sandbox = DockerSandbox(
        volume=settings.sandbox_volume,
        cache_volume=settings.sandbox_cache_volume,
        max_timeout_seconds=settings.sandbox_timeout_seconds,
        max_memory_mb=settings.sandbox_memory_mb,
        max_concurrent=settings.sandbox_max_concurrent,
    )
    # Checked once at startup rather than per scan. A misconfigured volume is
    # otherwise discovered as a tool that mysteriously finds nothing, which is
    # the single most expensive way for this to go wrong. The reason is handed
    # to NullSandbox so every errored tool check carries it — not just this log
    # line, which nobody sees until they go looking.
    if (reason := await asyncio.to_thread(sandbox.verify)) is not None:
        logger.error("sandbox unusable, tool checks will report errored", extra={"reason": reason})
        set_sandbox(NullSandbox(reason))
        return

    # A missing cache is a warning, not a refusal. Gitleaks, Hadolint and
    # Checkov need no cache and run regardless — Checkov's policy library
    # ships inside its image, and Hadolint has no external data at all; only
    # the Trivy and Semgrep checks report errored, which is the honest answer
    # while the warm services are still downloading.
    cache = settings.sandbox_cache_volume
    if cache and not await asyncio.to_thread(sandbox.volume_exists, cache):
        logger.warning(
            "sandbox cache volume is missing; tools that need it will report errored",
            extra={"volume": cache, "fix": "docker compose up warm-trivy warm-semgrep"},
        )

    set_sandbox(sandbox)
    logger.info(
        "sandbox ready",
        extra={
            "volume": settings.sandbox_volume or "(bind mount)",
            # Logged together because they are the memory ceiling for this
            # worker, and they are set in two different files. Their product is
            # the number worth knowing, and it is the number nobody computes
            # until something is killed for being over it.
            "max_concurrent": settings.sandbox_max_concurrent,
            "memory_mb": settings.sandbox_memory_mb,
            "peak_memory_mb": settings.sandbox_max_concurrent * settings.sandbox_memory_mb,
        },
    )


class WorkerSettings:
    functions = [run_scan]
    on_startup = on_startup

    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # A scan is mostly waiting on a clone and on subprocesses, so a worker can
    # hold several at once. Deliberately modest until there is a real measurement
    # to raise it against.
    max_jobs = 5

    # Cloning a large repository and running six scanners is minutes, not
    # seconds. arq's default of 300s would cancel and requeue mid-scan.
    job_timeout = 900

    # A scan is expensive and rarely worth repeating blindly — a repository that
    # fails to clone will fail again. Retrying once covers a transient network
    # blip without hammering anything.
    max_tries = 2
