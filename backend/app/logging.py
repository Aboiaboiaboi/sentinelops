"""Structured JSON logging.

Built on stdlib `logging` with a custom formatter rather than pulling in
structlog — the requirement is machine-readable output, and one formatter is a
smaller surface than a second logging framework layered over the first.

OpenTelemetry and trace correlation are deliberately not here. Those belong with
real deployment, and a tracing setup with nothing to export to is just overhead.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Everything the stdlib puts on a LogRecord. Any attribute outside this set was
# supplied by the caller through `extra={...}`, so it is application data and
# belongs in the emitted JSON.
_RESERVED_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
        # Not a stdlib attribute: uvicorn attaches a duplicate of the message
        # carrying ANSI colour codes, meant for its own terminal formatter.
        # Dropped rather than emitted — escape sequences in a structured log
        # field are noise at best and break downstream parsers at worst.
        "color_message",
    }
)


class JsonFormatter(logging.Formatter):
    """Renders a LogRecord as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            # ISO 8601, UTC, Z-suffixed — the same format the API returns in
            # response bodies. isoformat() renders UTC as "+00:00"; the swap to
            # "Z" keeps logs and responses byte-identical in shape.
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED_ATTRS})

        # default=str keeps a stray non-serialisable value in `extra` from raising
        # inside the logger, which would lose the log line entirely.
        return json.dumps(payload, default=str)


class AccessLogFilter(logging.Filter):
    """Lifts uvicorn's access-log positional args into named fields.

    Uvicorn formats access lines as a message with a fixed 5-tuple of args. Left
    alone they collapse into one opaque string, which defeats the point of JSON
    logs — you could not filter by status code. This unpacks them instead.

    Written defensively: uvicorn owns this tuple's shape, so a mismatch degrades
    to the plain message rather than breaking logging.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            client, method, path, http_version, status = args
            record.client = client
            record.method = method
            record.path = path
            record.http_version = http_version
            record.status_code = status
        return True


def configure_logging(level: str = "INFO") -> None:
    """Route every logger through one JSON handler on stdout.

    Called at startup, after uvicorn or arq have already configured logging for
    themselves. Both install their own text handlers, and a library handler left
    in place fires alongside this one — every line then appears twice, once as
    JSON and once as text, which makes the output unparseable as a whole.

    Every existing logger is stripped rather than a hardcoded list of library
    names: the previous version named only uvicorn's loggers, and arq's
    duplicate text output went unnoticed until the worker ran. Clearing whatever
    is actually there does not need updating for the next dependency.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for logger in logging.root.manager.loggerDict.values():
        # loggerDict holds PlaceHolder entries for namespaces that exist only as
        # a parent of a real logger; those have no handlers to clear.
        if not isinstance(logger, logging.Logger):
            continue
        logger.handlers = []
        logger.propagate = True

    # Outside the loop and unconditional: getLogger creates the access logger if
    # uvicorn has not yet, so the filter is attached whether or not it was
    # present above. Replacing the filter list keeps a second call idempotent.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.filters = [AccessLogFilter()]
