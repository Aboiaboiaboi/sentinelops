"""arq worker configuration.

Started with `arq app.workers.settings.WorkerSettings`. Kept separate from the
task definitions so importing a task does not drag in worker configuration —
the API imports task *names* as strings and never this module.
"""

from typing import Any

from arq.connections import RedisSettings

from app.config import get_settings
from app.logging import configure_logging
from app.workers.scan_tasks import run_scan

settings = get_settings()


async def on_startup(ctx: dict[str, Any]) -> None:
    """Reassert JSON logging.

    Redundant when started through `app.workers.main`, which configures logging
    before the worker exists. Kept because it is the only thing that helps if
    someone runs the arq CLI directly, where arq's own dictConfig would
    otherwise leave the output as plain text.
    """
    configure_logging(settings.log_level)


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
