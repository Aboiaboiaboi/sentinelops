"""Terraform / infrastructure-as-code misconfiguration with Checkov.

Answers `deployment.iac_misconfiguration`: a public S3 bucket, a security
group open to the world, an unencrypted volume — the things that show up in a
`.tf` file, not a Dockerfile. Nothing else in this project reads Terraform;
before this check, a repository deployed purely via Terraform was scored as
having no deployment configuration at all (see `parsing.is_terraform`).

**Exit code is not a signal — measured twice, and it disagreed with itself.**
A run against a small fixture with real findings and default flags exited
**0**. A run against this project's own real Terraform, with `--framework
terraform` set, exited **1** — with an equally valid, complete report on
stdout. A missing directory that crashes with a Python traceback also exits
**0**. There is no flag combination this was found to make trustworthy, so
the exit code is not checked at all beyond a log line; correctness comes
entirely from validating the JSON on stdout, which is genuinely empty (not
even `"{}"`) on a hard failure — a failed run surfaces as
`json.JSONDecodeError`, the same shape `trivy.py` relies on for the
unrelated reason that it deliberately never passes `--exit-code` either.

**Two different top-level shapes, and a third one avoided by a flag.**
Nothing to check (empty directory) returns the summary fields flat at the
top level. One matched framework returns
`{"check_type": ..., "results": {"failed_checks": [...], ...}, "summary": {...}}`.
**More than one matched framework returns a JSON array, one object per
framework** — measured against this project's own checkout, which has real
Terraform *and* a real Dockerfile, so an unrestricted run auto-detected both
and returned a list instead of a dict. `--framework terraform` (see `_spec()`)
forces the single-dict shape and also keeps this check from re-reporting
Dockerfile findings that are Hadolint's job. This check never actually sees
the empty-directory shape in practice either — it gates on discovered
`.tf`/`.tf.json` files before ever starting a container — but the parser
still checks for `"summary"` explicitly rather than assuming the nested shape,
since a shape assumption failing loudly as `errored` is cheap insurance.

**No severity field.** The open-source build reports `"severity": null` on
every finding — Bridgecrew's own severity data needs an API key this project
does not have and should not need for a local check. So impact is scaled by
how many checks failed, the same volume-surcharge idea `semgrep.py` and
`trivy.py` already use, just without a severity floor underneath it.

**Confirmed no hang risk under `--network=none`.** Checkov attempts one
network call on startup (Bridgecrew's hosted guideline metadata) even with
`--skip-download` set. Under the sandbox's `--network=none` there is no
network interface at all, so DNS resolution fails immediately
(`getaddrinfo` / `Temporary failure in name resolution`) rather than hanging
until a connect timeout — measured at under 3 seconds end to end against a
real `.tf` file, traceback and all, going to stderr where it is logged but
never persisted.

**No cache needed** — `needs_cache=False`, unlike Trivy and Semgrep. The
policy library ships inside the image; there is nothing to warm.
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
    flagged,
    passed,
    skipped,
)
from app.utils.sandbox import REPO_PLACEHOLDER, SandboxSpec, SandboxUnavailable, get_sandbox

logger = logging.getLogger(__name__)

IMAGE = "bridgecrew/checkov:3.2.334"
TIMEOUT_SECONDS = 120
BUDGET = 3

# Volume surcharge, same shape as semgrep.py: one misconfiguration is a
# mistake, several is a pattern. There is no severity to weight by (see the
# module docstring), so count is the whole signal.
_MANY = 5
_MANY_SURCHARGE = 1

_NO_IAC = "no infrastructure-as-code files were found to inspect"


def _spec() -> SandboxSpec:
    return SandboxSpec(
        image=IMAGE,
        command=(
            "--directory",
            REPO_PLACEHOLDER,
            # Without this, Checkov auto-detects every framework it finds
            # evidence of in the tree — Terraform, but also Dockerfiles,
            # GitHub Actions, whatever else — and when more than one matches,
            # its JSON output becomes a *list* of one object per framework
            # instead of a single object. Measured against this project's own
            # checkout, which has real Terraform and a real Dockerfile: the
            # unrestricted run returned a list; this one doesn't. Restricting
            # to Terraform also keeps this check from re-reporting Dockerfile
            # findings Hadolint already owns.
            "--framework",
            "terraform",
            "--output",
            "json",
            "--compact",
            # Both are the local, no-Bridgecrew-account equivalent of Semgrep's
            # --metrics=off: this project has no API key, does not want one,
            # and --network=none would fail either call anyway.
            "--skip-download",
            "--download-external-modules",
            "false",
        ),
        timeout_seconds=TIMEOUT_SECONDS,
        environment=(("HOME", "/tmp"),),
    )


def scan_iac(check: CheckSpec, repo: RepositoryIndex, has_iac_files: bool) -> CheckResult:
    """Run Checkov over the checkout and turn its report into one outcome.

    `has_iac_files` is asked before starting a container — the same
    correctness-first-speed-second ordering `semgrep.py` uses for
    `repo.production_files` — because Checkov happily reports a clean
    `resource_count: 0` for a directory with nothing to check, and answering
    *passed* for that would award marks for work nobody did.
    """
    if not has_iac_files:
        return skipped(check, _NO_IAC)

    try:
        result = get_sandbox().run(_spec(), repo_path=repo.path)
    except SandboxUnavailable as exc:
        return errored(check, f"the infrastructure scanner could not be run: {exc}")

    if result.timed_out:
        return errored(
            check, f"the infrastructure scanner did not finish within {TIMEOUT_SECONDS}s"
        )

    # No exit-code gate here — deliberately, and only after measuring it.
    # Checkov's exit code does not consistently mean "the run failed": in one
    # measured invocation a clean run against real findings returned 0, and in
    # another (otherwise identical) invocation with --framework set, a
    # perfectly valid report with real findings returned 1. The only signal
    # that has actually been reliable is whether stdout parses as the shape
    # below — the same reasoning trivy.py already applies for its own reason
    # (findings exit 0 there because --exit-code is deliberately not passed).
    # A genuinely broken run (crash, missing directory) still gets caught:
    # measured to produce empty stdout, not malformed JSON, so it falls
    # through to the JSONDecodeError branch just below.
    if result.exit_code != 0:
        logger.info("checkov exited non-zero", extra={"exit_code": result.exit_code})

    if result.truncated:
        return errored(check, "the infrastructure scanner produced more output than could be read")

    try:
        report = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        logger.warning("checkov produced unparseable output")
        return errored(check, "the infrastructure scanner produced a report that could not be read")

    if not isinstance(report, dict) or "summary" not in report:
        return errored(check, "the infrastructure scanner produced a report that could not be read")

    findings = _relevant_findings(report, result.repo_mount)
    logger.info(
        "checkov reported",
        extra={"failed": report.get("summary", {}).get("failed", 0), "kept": len(findings)},
    )

    if not findings:
        return passed(check)

    return flagged(check, _finding(findings))


def _relevant_findings(report: dict, repo_mount: str) -> list[tuple[str, str, str]]:
    """(check id, check name, repository-relative path) for each failed check."""
    failed_checks = (report.get("results") or {}).get("failed_checks") or []
    findings: list[tuple[str, str, str]] = []
    for entry in failed_checks:
        if not isinstance(entry, dict):
            continue
        path = _repository_path(str(entry.get("file_path") or ""), repo_mount)
        check_id = str(entry.get("check_id") or "unknown")
        name = str(entry.get("check_name") or "")
        findings.append((check_id, name, path))
    return findings


def _repository_path(reported: str, repo_mount: str) -> str:
    path = reported.replace("\\", "/")
    if repo_mount and path.startswith(repo_mount):
        path = path[len(repo_mount) :]
    return path.lstrip("/")


def score_impact(findings: list[tuple[str, str, str]]) -> int:
    """What this check deducts. Never more than its own budget."""
    base = BUDGET - _MANY_SURCHARGE
    surcharge = _MANY_SURCHARGE if len(findings) >= _MANY else 0
    return min(BUDGET, base + surcharge)


def _finding(findings: list[tuple[str, str, str]]) -> ScanFinding:
    check_id, name, path = findings[0]
    others = (
        f" It is one of {len(findings)} Checkov policies this project's Terraform does not pass."
        if len(findings) > 1
        else ""
    )

    return ScanFinding(
        category="deployment",
        severity=Severity.HIGH,
        title="Infrastructure misconfiguration found",
        description=(
            f"{path}: {check_id} — {name}.{others} This is a property of the infrastructure "
            "itself, not the application — a public bucket or an open security group is "
            "exploitable the moment it's applied, independent of anything the code does."
        ),
        recommendation=(
            "Run `checkov -d .` locally against the Terraform and work through its findings — "
            "each one names the resource and the specific setting to change."
        ),
        score_impact=score_impact(findings),
    )
