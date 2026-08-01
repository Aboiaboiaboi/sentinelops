"""Dangerous code patterns with Semgrep.

Answers `security.code_patterns`: does this code contain shapes that are
dangerous regardless of what the values are — a shell invoked with user input, a
query built by string formatting, a token compared with `==`. Regex cannot ask
this, because the answer depends on the structure of the code rather than on any
text in it.

**Exit codes, measured before this was written:**

- findings, or none -> **0** in both cases
- ruleset file missing -> 7
- target unreadable -> 2

Zero is the only success.

**The ruleset is a file, never `--config p/security-audit`.** Semgrep caches
nothing and re-fetches registry rules on every run, which a container with no
network cannot do. The warm step materialises the resolved ruleset into the
cache volume, and this reads it from there.

**`HOME` has to be set.** Semgrep writes a settings file at startup; with no
HOME it resolves to `/.semgrep` on the read-only root filesystem and dies with
an OSError before scanning anything. It also shells out to git, which wants a
writable config path for the same reason.
"""

import json
import logging
from collections.abc import Iterable
from typing import Any

from app.scanners.base import (
    CheckResult,
    CheckSpec,
    RepositoryIndex,
    ScanFinding,
    Severity,
    errored,
    failed,
    passed,
    skipped,
)
from app.utils.sandbox import (
    CACHE_MOUNT,
    REPO_PLACEHOLDER,
    SandboxResult,
    SandboxSpec,
    SandboxUnavailable,
    get_sandbox,
)

logger = logging.getLogger(__name__)

IMAGE = "semgrep/semgrep:1.171.0"

# Written into the cache volume by the warm service in docker-compose.yml.
RULES_PATH = f"{CACHE_MOUNT}/semgrep/security-audit.yml"

# The slowest of the three by a wide margin: it parses every source file and
# runs 225 rules over the AST. Still bounded by the operator's ceiling.
TIMEOUT_SECONDS = 600

BUDGET = 4

# Only ERROR-severity rules are reported. `p/security-audit` at full volume is
# noisy — WARNING and INFO include a great deal of "consider whether" advice —
# and noise is how a scanner earns a reputation for crying wolf, the failure
# this project has spent three phases avoiding. Curating a local rule set is the
# follow-up if ERROR alone still turns out to be too loud.
_REPORTED_SEVERITY = "ERROR"

# Because --config takes a file path, Semgrep derives every rule id from that
# path: `python.lang.security...` comes back as
# `cache.semgrep.python.lang.security...`. Stripped so a finding names the rule
# rather than our cache layout.
_RULE_ID_PREFIX = "cache.semgrep."

# Volume surcharge, the same shape Trivy uses: one dangerous pattern is a bug,
# many is a habit. Cannot dominate, because kind matters more than count.
_MANY = 10
_MANY_SURCHARGE = 1


def _spec() -> SandboxSpec:
    return SandboxSpec(
        image=IMAGE,
        command=(
            "semgrep",
            "scan",
            "--config",
            RULES_PATH,
            "--json",
            "--quiet",
            # Both of these are network calls that would fail in the sandbox and
            # take the run down with them.
            "--metrics=off",
            "--disable-version-check",
            REPO_PLACEHOLDER,
        ),
        timeout_seconds=TIMEOUT_SECONDS,
        needs_cache=True,
        # See the module docstring: without this Semgrep dies before scanning.
        environment=(("HOME", "/tmp"),),
    )


#: Nothing for a code-pattern rule to match against.
NO_SOURCE = "the repository has no hand-written source files"

#: Source exists, but none of it is in a language these rules cover.
NO_SUPPORTED_SOURCE = "no source files were found that the rules apply to"


def scan_code_patterns(check: CheckSpec, repo: RepositoryIndex) -> CheckResult:
    """Run Semgrep over the checkout and turn its report into one outcome."""
    # Asked before starting a container, for correctness first and speed second.
    # Semgrep counts a README as a scanned path, so its own report cannot tell
    # "there is no code here" from "the code is fine" — and answering *passed*
    # for a repository of documentation would award marks for work nobody did.
    if not repo.production_files:
        return skipped(check, NO_SOURCE)

    try:
        result = get_sandbox().run(_spec(), repo_path=repo.path)
    except SandboxUnavailable as exc:
        return errored(check, f"the code pattern scanner could not be run: {exc}")

    if result.timed_out:
        return errored(check, f"the code pattern scanner did not finish within {TIMEOUT_SECONDS}s")

    if result.exit_code != 0:
        logger.warning(
            "semgrep failed",
            extra={"exit_code": result.exit_code, "stderr": result.stderr[:2000]},
        )
        return errored(check, "the code pattern scanner exited unexpectedly")

    if result.truncated:
        return errored(check, "the code pattern scanner produced more output than could be read")

    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        logger.warning("semgrep produced unparseable output")
        return errored(check, "the code pattern scanner produced a report that could not be read")

    if not isinstance(report, dict):
        return errored(check, "the code pattern scanner produced a report that could not be read")

    if not (report.get("paths") or {}).get("scanned"):
        # There is source, but Semgrep opened none of it — a language these
        # rules do not cover. Nothing was assessed, so nothing is claimed.
        return skipped(check, NO_SUPPORTED_SOURCE)

    matches = _relevant_matches(report.get("results") or [], repo, result)
    logger.info(
        "semgrep reported",
        extra={"raw": len(report.get("results") or []), "kept": len(matches)},
    )

    if not matches:
        return passed(check)

    return failed(check, _finding(matches))


def _relevant_matches(
    results: Iterable[Any], repo: RepositoryIndex, sandbox_result: SandboxResult
) -> list[tuple[str, str]]:
    """(rule, repository-relative path) for every match worth reporting.

    Filtered to production files — the index's own set of hand-written,
    non-test source. That excludes the test suite and machine-generated code in
    one move, which is the same standard every other check in this category
    holds: a deliberate `shell=True` in a test fixture is a fixture, and "fix
    this 4000-line generated client" is not advice anyone can act on.
    """
    production = {repo.relative(path) for path in repo.production_files}
    matches: list[tuple[str, str]] = []

    for entry in results:
        if not isinstance(entry, dict):
            continue
        if str((entry.get("extra") or {}).get("severity") or "").upper() != _REPORTED_SEVERITY:
            continue

        path = _repository_path(str(entry.get("path") or ""), sandbox_result.repo_mount)
        if path not in production:
            continue

        rule = str(entry.get("check_id") or "unknown").removeprefix(_RULE_ID_PREFIX)
        matches.append((rule, path))

    return matches


def _repository_path(reported: str, repo_mount: str) -> str:
    path = reported.replace("\\", "/")
    if repo_mount and path.startswith(repo_mount):
        path = path[len(repo_mount) :]
    return path.lstrip("/")


def _describe_rule(rule: str) -> str:
    """The last, most specific segment of a rule id, as words.

    `python.lang.security.audit.subprocess-shell-true.subprocess-shell-true`
    becomes `subprocess shell true` — the taxonomy above it is Semgrep's
    filing system, not information for the person reading the finding.
    """
    return rule.rsplit(".", 1)[-1].replace("-", " ").replace("_", " ")


def score_impact(matches: list[tuple[str, str]]) -> int:
    """What this check deducts. Never more than its own budget."""
    base = BUDGET - _MANY_SURCHARGE
    surcharge = _MANY_SURCHARGE if len(matches) >= _MANY else 0
    return min(BUDGET, base + surcharge)


def _finding(matches: list[tuple[str, str]]) -> ScanFinding:
    by_rule: dict[str, list[str]] = {}
    for rule, path in matches:
        by_rule.setdefault(rule, []).append(path)

    ranked = sorted(by_rule.items(), key=lambda item: (-len(item[1]), item[0]))
    headline_rule, headline_paths = ranked[0]

    others = ""
    if len(matches) > 1:
        others = (
            f" It is the worst of {len(matches)} matches across "
            f"{len(by_rule)} rule{'s' if len(by_rule) > 1 else ''}."
        )

    return ScanFinding(
        category="security",
        severity=Severity.HIGH,
        title="Dangerous code patterns found",
        description=(
            f"{headline_paths[0]} matches {_describe_rule(headline_rule)}.{others} "
            "These are structural weaknesses — the shape of the code is unsafe whatever "
            "values flow through it — and only the rules Semgrep rates as errors are "
            "reported here, so each one is worth reading rather than dismissing."
        ),
        recommendation=(
            "Open the file at the reported line and judge whether the input can be influenced "
            "from outside the process. If it can, this is exploitable and worth fixing now; if "
            "it genuinely cannot, the pattern is still worth replacing, because the next person "
            "to touch the code will not know that."
        ),
        score_impact=score_impact(matches),
    )
