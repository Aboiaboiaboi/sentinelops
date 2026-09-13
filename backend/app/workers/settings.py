"""arq worker configuration.

Started with `arq app.workers.settings.WorkerSettings`. Kept separate from the
task definitions so importing a task does not drag in worker configuration —
the API imports task *names* as strings and never this module.
"""

import asyncio
import logging
from typing import Any

from arq.connections import RedisSettings

from app.broker.client import BrokerSandbox
from app.config import get_settings
from app.logging import configure_logging
from app.utils.sandbox import NullSandbox, set_sandbox
from app.workers.scan_tasks import run_scan

logger = logging.getLogger(__name__)

settings = get_settings()


async def on_startup(ctx: dict[str, Any]) -> None:
    """Reassert JSON logging, and connect to the scan broker.

    Logging is redundant when started through `app.workers.main`, which
    configures it before the worker exists. Kept because it is the only thing
    that helps if someone runs the arq CLI directly, where arq's own dictConfig
    would otherwise leave the output as plain text.

    The sandbox connection is installed here and nowhere else. The API never
    runs a tool — it does not even have git — so it has no need to reach the
    broker at all.

    This worker never touches Docker directly any more: it holds a
    BrokerSandbox pointed at a Unix socket the broker — a host-level process
    started by systemd, not by Compose — owns. See app/broker/ for why.
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

    sandbox = BrokerSandbox(settings.sandbox_broker_socket)
    # Checked once at startup rather than per scan. The reason is handed to
    # NullSandbox so every errored tool check carries it — not just this log
    # line, which nobody sees until they go looking.
    if (reason := await asyncio.to_thread(sandbox.health)) is not None:
        logger.error(
            "scan broker unusable, tool checks will report errored", extra={"reason": reason}
        )
        set_sandbox(NullSandbox(reason))
        return

    set_sandbox(sandbox)
    logger.info("sandbox ready", extra={"broker_socket": settings.sandbox_broker_socket})


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
