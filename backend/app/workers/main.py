"""Worker entrypoint.

Run with `python -m app.workers.main` rather than arq's own `arq ...` CLI.

The reason is logging order. arq's CLI imports the settings module first and
calls logging.config.dictConfig afterwards, so any configuration done at import
time is overwritten — and its "Starting worker" and Redis banner lines are then
emitted before the on_startup hook can put the JSON handler back. Two plain-text
lines in an otherwise structured stream is enough to break a log parser.

`run_worker` itself does no logging configuration, so calling configure_logging
before it means every line the worker ever writes is JSON, including the banner.
"""

from arq import run_worker

from app.config import get_settings
from app.logging import configure_logging
from app.workers.settings import WorkerSettings


def main() -> None:
    configure_logging(get_settings().log_level)
    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
