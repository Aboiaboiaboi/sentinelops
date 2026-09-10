"""Dockerfile linting with Hadolint.

Answers `deployment.dockerfile_lint`: the things a real Dockerfile linter
catches that this project's own hand-rolled Dockerfile checks (in
`deployment/scanner.py` and `deployment/parsing.py`) do not — unpinned
`apt-get install` versions, `apt-get upgrade` baked into a layer, a missing
`--no-install-recommends`, `ADD` where `COPY` belongs, `cd` instead of
`WORKDIR`, and ShellCheck findings inside `RUN` lines.

**Deliberately does not report everything Hadolint can.** Four of its rules
overlap checks this project already runs, and in all four cases the existing
check is more precise — reporting both would double the same finding under
two headings:

- `DL3002` (last USER should not be root) only fires on an *explicit*
  `USER root`. `deployment.non_root` also catches the far more common case of
  no `USER` line at all, which is Docker's actual default and Hadolint stays
  silent on.
- `DL3006`/`DL3007` (pin the FROM image) cannot see Compose files.
  `deployment.image_pinning` checks both.
- `DL3025` (JSON form CMD/ENTRYPOINT) does not know about init wrappers.
  `deployment.signal_handling` passes `ENTRYPOINT ["tini", "--", ...]`
  correctly; Hadolint would flag it.

So `DL3002`, `DL3006`, `DL3007` and `DL3025` are passed to `--ignore`.

**Exit codes, measured before this was written** (real container,
`hadolint/hadolint:v2.12.0-alpine`):

- clean Dockerfile -> **0**
- findings, default flags -> **1**
- `--no-fail` set -> **0** in both of the above — findings no longer affect
  the exit code at all, so a non-zero exit with `--no-fail` set means the run
  itself failed, not that it found something. The same shape as gitleaks'
  `--exit-code 0`, solving the identical ambiguity a different way, because
  Hadolint has no equivalent flag that keeps exit 0 for findings specifically.
- missing/unreadable file -> **1**, a plain-text error on stdout, not JSON

Zero-with-`--no-fail` is the only success.

**Takes file arguments, not a directory.** Unlike the other three tools,
Hadolint has no "scan this whole tree" mode, and the sandbox has no shell and
no glob expansion — argv goes straight to `docker run`. So `_spec()` is built
per-scan from the Dockerfile paths `deployment/scanner.py` already discovered
in its single walk over `repo.files`, not from a fresh directory scan here.
Measured as fast (<1s) even with several files in one invocation, so there is
no cost to asking for all of them in one container rather than one per file.
"""

import json
import logging

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
from app.utils.sandbox import REPO_PLACEHOLDER, SandboxSpec, SandboxUnavailable, get_sandbox

logger = logging.getLogger(__name__)

IMAGE = "hadolint/hadolint:v2.12.0-alpine"
TIMEOUT_SECONDS = 60
BUDGET = 1

# Overlaps existing, more precise checks — see the module docstring.
_MUTED_RULES = ("DL3002", "DL3006", "DL3007", "DL3025")

# Info/style-level rules are noise at this project's severity bar — the same
# discipline Semgrep's ERROR-only filter applies. Anything worth a finding
# here is at least a warning.
_REPORTED_LEVELS = frozenset({"warning", "error"})

# A ceiling on how many Dockerfiles go into one invocation, matching
# `deployment/scanner.py`'s `_MAX_MANIFESTS`. A monorepo with dozens of
# services would otherwise turn one lint pass into an unbounded argv.
_MAX_DOCKERFILES = 50

_NO_DOCKERFILE = "no Dockerfile was found to inspect"


def _spec(dockerfiles: list[str]) -> SandboxSpec:
    command = ["hadolint", "--no-fail", "--format", "json"]
    for rule in _MUTED_RULES:
        command += ["--ignore", rule]
    command += [f"{REPO_PLACEHOLDER}/{relative}" for relative in dockerfiles[:_MAX_DOCKERFILES]]
    return SandboxSpec(image=IMAGE, command=tuple(command), timeout_seconds=TIMEOUT_SECONDS)


def scan_dockerfiles(
    check: CheckSpec, repo: RepositoryIndex, dockerfile_paths: list
) -> CheckResult:
    """Run Hadolint over the Dockerfiles the deployment scanner already found."""
    if not dockerfile_paths:
        return skipped(check, _NO_DOCKERFILE)

    relatives = [repo.relative(path) for path in dockerfile_paths]

    try:
        result = get_sandbox().run(_spec(relatives), repo_path=repo.path)
    except SandboxUnavailable as exc:
        return errored(check, f"the Dockerfile linter could not be run: {exc}")

    if result.timed_out:
        return errored(check, f"the Dockerfile linter did not finish within {TIMEOUT_SECONDS}s")

    if result.exit_code != 0:
        logger.warning(
            "hadolint failed",
            extra={"exit_code": result.exit_code, "stderr": result.stderr[:2000]},
        )
        return errored(check, "the Dockerfile linter exited unexpectedly")

    if result.truncated:
        return errored(check, "the Dockerfile linter produced more output than could be read")

    try:
        report = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        logger.warning("hadolint produced unparseable output")
        return errored(check, "the Dockerfile linter produced a report that could not be read")

    if not isinstance(report, list):
        return errored(check, "the Dockerfile linter produced a report that could not be read")

    findings = _relevant_findings(report, result.repo_mount)
    logger.info("hadolint reported", extra={"raw": len(report), "kept": len(findings)})

    if not findings:
        return passed(check)

    return failed(check, _finding(findings))


def _relevant_findings(report: list, repo_mount: str) -> list[tuple[str, str, str]]:
    """(rule code, message, repository-relative path) for each kept entry."""
    findings: list[tuple[str, str, str]] = []
    for entry in report:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("level") or "").lower() not in _REPORTED_LEVELS:
            continue
        path = _repository_path(str(entry.get("file") or ""), repo_mount)
        code = str(entry.get("code") or "unknown")
        message = str(entry.get("message") or "")
        findings.append((code, message, path))
    return findings


def _repository_path(reported: str, repo_mount: str) -> str:
    path = reported.replace("\\", "/")
    if repo_mount and path.startswith(repo_mount):
        path = path[len(repo_mount) :]
    return path.lstrip("/")


def score_impact(findings: list[tuple[str, str, str]]) -> int:
    """Flat — this check's whole budget is one point, so there is nothing to
    grade between one finding and several."""
    return BUDGET if findings else 0


def _finding(findings: list[tuple[str, str, str]]) -> ScanFinding:
    code, message, path = findings[0]
    others = f" ({len(findings) - 1} more)" if len(findings) > 1 else ""
    return ScanFinding(
        category="deployment",
        severity=Severity.LOW,
        title="Dockerfile lint findings",
        description=(
            f"{path}: {code} — {message}{others} Hadolint checks layer hygiene and shells "
            "out to ShellCheck for RUN lines — things a structural Dockerfile parse doesn't "
            "cover, like unpinned system packages and unsafe shell scripting."
        ),
        recommendation=(
            "Run `hadolint` locally against the Dockerfile and work through its findings — "
            "each one names the exact line and rule."
        ),
        score_impact=score_impact(findings),
    )
