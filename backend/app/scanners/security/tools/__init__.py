"""Security tools that run in a sandbox.

One module per tool. Each owns three things and nothing else: the `SandboxSpec`
that runs it, the parsing of its output, and the translation of that output into
a `CheckResult`.

They are the only place in `scanners/` that reaches outside the checkout, and
they do it through `utils/sandbox.py` rather than by starting a process, so the
scanners stay testable against a directory and a fake runner. A tool that cannot
run reports `errored` — never `passed`, which would tell somebody their
repository is clean when nothing looked at it.
"""

from app.scanners.security.tools.gitleaks import scan_for_secrets
from app.scanners.security.tools.trivy import scan_dependencies

__all__ = ["scan_dependencies", "scan_for_secrets"]
