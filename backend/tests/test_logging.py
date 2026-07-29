"""Tests for logging configuration.

The duplicate-handler case is the one that matters. Uvicorn and arq each install
their own text handler before the app configures logging; any left in place
fires alongside ours, so every line appears twice — once as JSON and once as
plain text — and the output stops being parseable as a whole. That went
unnoticed until the worker ran, because the original implementation cleared a
hardcoded list of uvicorn's loggers and arq was not on it.
"""

import json
import logging

from app.logging import JsonFormatter, configure_logging


def _record(**kwargs) -> logging.LogRecord:
    defaults = {
        "name": "test",
        "level": logging.INFO,
        "pathname": __file__,
        "lineno": 1,
        "msg": "hello",
        "args": (),
        "exc_info": None,
    }
    return logging.LogRecord(**{**defaults, **kwargs})


class TestJsonFormatter:
    def test_emits_parseable_json(self) -> None:
        payload = json.loads(JsonFormatter().format(_record()))

        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"

    def test_timestamp_is_z_suffixed(self) -> None:
        """Same format the API returns in response bodies, so logs and responses
        can be correlated without reconciling two timestamp shapes."""
        payload = json.loads(JsonFormatter().format(_record()))

        assert payload["timestamp"].endswith("Z")

    def test_extra_fields_are_included(self) -> None:
        record = _record()
        record.scan_id = "abc"

        assert json.loads(JsonFormatter().format(record))["scan_id"] == "abc"

    def test_non_serialisable_extra_does_not_break_the_line(self) -> None:
        """Raising inside the formatter would lose the log line entirely."""
        record = _record()
        record.thing = object()

        assert "thing" in json.loads(JsonFormatter().format(record))


class TestConfigureLogging:
    def test_leaves_no_library_handlers_behind(self) -> None:
        """A library handler surviving means every line is logged twice.

        Simulates what uvicorn and arq both do — install a handler on their own
        logger before the app configures logging.
        """
        for name in ("uvicorn", "uvicorn.access", "arq", "arq.worker"):
            logging.getLogger(name).handlers = [logging.StreamHandler()]

        configure_logging("INFO")

        offenders = [
            name
            for name, logger in logging.root.manager.loggerDict.items()
            if isinstance(logger, logging.Logger) and logger.handlers
        ]
        assert offenders == []

    def test_everything_propagates_to_the_root_handler(self) -> None:
        logging.getLogger("arq.worker").propagate = False

        configure_logging("INFO")

        assert logging.getLogger("arq.worker").propagate is True

    def test_root_has_exactly_one_json_handler(self) -> None:
        configure_logging("INFO")
        configure_logging("INFO")

        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)

    def test_access_filter_is_attached_once(self) -> None:
        """Attached unconditionally, since uvicorn may not have created the
        logger yet — and not duplicated when configure_logging runs twice."""
        configure_logging("INFO")
        configure_logging("INFO")

        assert len(logging.getLogger("uvicorn.access").filters) == 1
