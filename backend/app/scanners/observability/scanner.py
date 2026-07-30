"""Observability checks.

When this is running in production and behaving strangely, can anybody find out
why — or does diagnosis start with adding logging and deploying again.

Evidence is taken from declared dependencies as well as source. A library can be
imported in one file out of five hundred, but it is always in the manifest, so
the manifest is the more reliable signal of the two.
"""

import re

from app.scanners.base import RepositoryIndex, ScanFinding, Severity

CATEGORY = "observability"

# Impacts. The worst case — nothing logs and nothing is measured — totals the
# category weight of 10. Having plain-text logging is deliberately cheaper than
# having none: it is worse to be silent than to be hard to query.
_NO_LOGGING = 6
_UNSTRUCTURED_LOGGING = 4
_NO_METRICS_OR_ERROR_TRACKING = 4

# Anything that is a real logger rather than a print statement.
_LOGGING_EVIDENCE = re.compile(
    r"\b(?:"
    r"import\s+logging|logging\.getLogger|getLogger\(|logger\b|LOGGER\b"
    r"|winston|pino|bunyan|log4js|loglevel"
    r"|log/slog|slog\.|zap\.|logrus|zerolog"
    r"|slf4j|logback|log4j|Serilog|NLog|Microsoft\.Extensions\.Logging"
    r"|Rails\.logger|ActiveSupport::Logger|Monolog"
    r")",
    re.IGNORECASE,
)

# Evidence that log output is machine-readable rather than prose.
_STRUCTURED_EVIDENCE = re.compile(
    r"\b(?:"
    r"structlog|python-json-logger|pythonjsonlogger|JsonFormatter|json_formatter"
    r"|jsonlogger|ecs-logging|logstash"
    r"|pino|format\.json|winston\.format|JSONHandler|NewJSONEncoder"
    r"|zap\.NewProduction|zerolog|CompactJsonFormatter|logfmt"
    r"|structured.?log"
    r")",
    re.IGNORECASE,
)

# Metrics, tracing, and error reporting all answer "how do you find out".
_TELEMETRY_EVIDENCE = re.compile(
    r"\b(?:"
    r"prometheus|prom-client|prometheus_client|micrometer|statsd|dogstatsd"
    r"|opentelemetry|otel|opencensus|jaeger|zipkin"
    r"|sentry|sentry_sdk|@sentry|rollbar|bugsnag|airbrake|honeybadger"
    r"|newrelic|new_relic|datadog|ddtrace|elastic-apm|elasticapm|appsignal"
    r")",
    re.IGNORECASE,
)


# A line that brings a dependency into scope, across ecosystems.
_IMPORT_LINE = re.compile(
    r"^\s*(?:import|from|use|using|require|@import)\b|require\s*\(|from\s+['\"]",
)

# Actually calling a logger, as opposed to mentioning one. `logger.info(...)` is
# evidence; the word "logger" in a sentence is not.
_LOG_CALL = re.compile(
    r"\b(?:logger|logging|log|LOG|Log)\s*\.\s*"
    r"(?:debug|info|warn|warning|error|critical|exception|fatal|trace)\s*\(",
)

# Configuring structured output, as opposed to naming a library that does it.
_STRUCTURED_CALL = re.compile(
    r"\b(?:JsonFormatter|JSONFormatter|jsonlogger|JSONHandler|CompactJsonFormatter)\s*\(",
)


class ObservabilityScanner:
    category = CATEGORY

    def scan(self, repo: RepositoryIndex) -> list[ScanFinding]:
        # Evidence has to be an import, a manifest entry, or a call site —
        # never prose. Searching raw source found "OpenTelemetry" inside a
        # docstring that said OpenTelemetry was deliberately *not* used, and
        # matched library names inside this scanner's own pattern definitions.
        # Both read as "the project uses this" when the opposite was true.
        manifests = repo.manifest_text()
        imports: list[str] = []
        calls: list[str] = []

        for path in repo.production_files:
            for line in repo.read(path).splitlines():
                if _IMPORT_LINE.search(line):
                    imports.append(line)
                elif _LOG_CALL.search(line) or _STRUCTURED_CALL.search(line):
                    calls.append(line)

        declared = manifests + "\n".join(imports)
        used = "\n".join(calls)

        has_logging = bool(_LOGGING_EVIDENCE.search(declared) or _LOG_CALL.search(used))
        has_structured = bool(
            _STRUCTURED_EVIDENCE.search(declared) or _STRUCTURED_CALL.search(used)
        )
        has_telemetry = bool(_TELEMETRY_EVIDENCE.search(declared))

        findings: list[ScanFinding] = []
        if not has_logging:
            findings.append(self._no_logging())
        elif repo.is_service and not has_structured:
            # Only reported when logging exists — "unstructured" is not a
            # meaningful complaint about a project that logs nothing at all.
            findings.append(self._unstructured_logging())

        if repo.is_service and not has_telemetry:
            findings.append(self._no_telemetry())
        return findings

    def _no_logging(self) -> ScanFinding:
        return ScanFinding(
            category=CATEGORY,
            severity=Severity.HIGH,
            title="No logging",
            description=(
                "No logging framework was found in the dependencies or the source. When something "
                "goes wrong in production there is no record that it happened, so diagnosis starts "
                "with adding logging and deploying again — by which point the conditions that "
                "caused it are usually gone."
            ),
            recommendation=(
                "Adopt the standard logging library for your stack and log at the boundaries: "
                "requests in, calls out, and anything unexpected."
            ),
            score_impact=_NO_LOGGING,
        )

    def _unstructured_logging(self) -> ScanFinding:
        return ScanFinding(
            category=CATEGORY,
            severity=Severity.MEDIUM,
            title="Logs are not structured",
            description=(
                "Logging is in place but nothing suggests the output is machine-readable. Plain "
                'prose cannot be filtered or aggregated, so a question like "show me every '
                'failure for this user in the last hour" becomes grep across instances.'
            ),
            recommendation=(
                "Emit JSON with a consistent set of fields, and attach identifiers — request id, "
                "user id — so lines can be correlated rather than read one by one."
            ),
            score_impact=_UNSTRUCTURED_LOGGING,
        )

    def _no_telemetry(self) -> ScanFinding:
        return ScanFinding(
            category=CATEGORY,
            severity=Severity.MEDIUM,
            title="No metrics or error tracking",
            description=(
                "No metrics, tracing, or error-reporting library was found. Nothing measures "
                "latency, error rate or throughput, and no exception is reported anywhere — so "
                "the first notice that the service is broken comes from whoever is using it."
            ),
            recommendation=(
                "Export a handful of metrics that describe user-visible health, and send "
                "unhandled exceptions to an error tracker so they are seen without being hunted."
            ),
            score_impact=_NO_METRICS_OR_ERROR_TRACKING,
        )
