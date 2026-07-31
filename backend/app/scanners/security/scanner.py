"""Baseline security checks.

Deliberately shallow, and temporarily so: Phase 3 replaces this with real
tooling — Gitleaks for secrets, Trivy for dependency vulnerabilities, Semgrep
for code patterns — each running sandboxed. What lives here is the subset that
regex over a checkout can do *responsibly*: committed credential files, token
formats that cannot be anything else, debug mode, and TLS verification switched
off.

The false-positive bar is higher in this category than anywhere else. Telling
someone they leaked a credential when they did not is alarming in a way "your
README is missing" is not, and the second false alarm is when they stop
believing the tool. Every check here prefers silence to a guess.
"""

import re
from pathlib import Path

from app.scanners.base import (
    CheckResult,
    CheckSpec,
    RepositoryIndex,
    ScanFinding,
    Severity,
    code_only,
    failed,
    is_test_file,
    passed,
    skipped,
)

CATEGORY = "security"

_CREDENTIAL_FILES = CheckSpec("security.credential_files", "No credential files committed")
_HARDCODED = CheckSpec("security.hardcoded_secrets", "No secrets hardcoded in source")
_DEBUG = CheckSpec("security.debug_mode", "Debug mode off")
_TLS = CheckSpec("security.tls_verification", "TLS verification enabled")
_CONTAINER = CheckSpec("security.container_secrets", "No secrets in container configuration")
_GITIGNORE = CheckSpec("security.gitignore", "Env files protected by .gitignore")

# The source-reading checks need source to read. A repository of configuration
# and documentation is not passing them — they never ran.
_NO_SOURCE = "the repository has no hand-written source files"

# Impacts, summing to the category weight of 25.
_CREDENTIAL_FILE = 6
_HARDCODED_SECRET = 6
_DEBUG_ENABLED = 4
_TLS_DISABLED = 4
_SECRET_IN_CONTAINER_CONFIG = 3
_NO_GITIGNORE_PROTECTION = 2


# --- Committed credential files --------------------------------------------
#
# A filename alone is not evidence. Dogfooding against real repositories made
# that concrete: Sentry's self-hosted commits a `.env` of documented defaults,
# tiangolo's FastAPI template commits one where every secret is `changethis`,
# and Insomnia's `.npmrc` holds `${NODE_AUTH_TOKEN}` — interpolation, the
# correct pattern. All three would have been CRITICAL findings on well-run
# projects. So the name selects *candidates*, and only content convicts.

# Names that are credentials by definition, with no innocent variant to
# inspect for. A committed id_rsa is a leaked key; a committed .htpasswd is a
# file of password hashes.
_ALWAYS_CREDENTIAL_NAMES = frozenset({"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".htpasswd"})

# Package-manager configuration that *may* carry an auth token beside harmless
# registry settings. Flagged only when a literal credential is present.
_CONFIG_CREDENTIAL_NAMES = frozenset({".npmrc", ".pypirc", ".netrc"})
_CONFIG_SECRET_LINE = re.compile(
    r"(?:_authToken|_password|password)\s*[:=\s]\s*(?P<value>\S{8,})", re.IGNORECASE
)

# Cloud credential JSON, identified by the fields only a real credential has.
_JSON_CREDENTIAL_NAMES = frozenset(
    {"credentials.json", "service-account.json", "serviceaccount.json", "gcloud-key.json"}
)
_JSON_SECRET_FIELD = re.compile(r'"(?:private_key|client_secret|refresh_token)"\s*:\s*"[^"]{8,}')

# A line in an env file that sets a secret-named variable to something real.
# Horizontal whitespace only around the `=`: `\s` matches newlines, which let
# an *empty* assignment swallow the following line as its "value" — turning
# `SMTP_PASSWORD=` + `RETENTION_DAYS=90` into a conviction.
_ENV_SECRET_LINE = re.compile(
    r"""^[A-Za-z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|CREDENTIALS?)
        [A-Za-z0-9_]*[ \t]*=[ \t]*(?P<value>.+)$""",
    re.MULTILINE | re.IGNORECASE | re.VERBOSE,
)

# The env-file convention that is *correct*: a committed template with the
# secrets blanked. Never flagged, and its presence is a good sign.
_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")

# Extensions that may hold a private key — but only the file's own header can
# say so. A .pem is just as often a public certificate, which is supposed to be
# committed; flagging on extension alone would hit every TLS-terminating
# project.
_KEY_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"})
_PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN (?:[A-Z ]*)?PRIVATE KEY-----")

# Binary keystore formats cannot be grepped for a header; the extension is the
# evidence, and there is no public-certificate variant to confuse them with.
_BINARY_KEY_EXTENSIONS = frozenset({".p12", ".pfx", ".jks", ".keystore"})


# --- Hardcoded secrets ------------------------------------------------------

# Tier A: token formats that cannot be anything except what they are. Each of
# these is issued by one provider with one prefix, so a match is a credential —
# the only question is whether it is live.
_KNOWN_TOKEN_FORMATS = re.compile(
    r"AKIA[0-9A-Z]{16}"  # AWS access key id
    r"|ghp_[A-Za-z0-9]{36}"  # GitHub personal access token
    r"|gho_[A-Za-z0-9]{36}"  # GitHub OAuth token
    r"|github_pat_[A-Za-z0-9_]{22,}"  # GitHub fine-grained PAT
    r"|sk_live_[A-Za-z0-9]{24,}"  # Stripe live secret key
    r"|rk_live_[A-Za-z0-9]{24,}"  # Stripe live restricted key
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack tokens
    r"|AIza[0-9A-Za-z_-]{35}"  # Google API key
    r"|ya29\.[0-9A-Za-z_-]{20,}"  # Google OAuth access token
    r"|glpat-[A-Za-z0-9_-]{20,}"  # GitLab PAT
    r"|npm_[A-Za-z0-9]{36}"  # npm token
)

# Tier B: an assignment whose left side names a secret and whose right side is a
# literal. Where small-project leaks actually live, and also the riskiest
# pattern in this file — hence the guards below.
_SECRET_ASSIGNMENT = re.compile(
    r"""(?:password|passwd|api_key|apikey|api_secret|secret_key|secretkey
         |auth_token|access_token|private_key|client_secret|db_password
         |database_password|admin_password)
        \s*[:=]\s*
        (?P<quote>["'])(?P<value>[^"']{8,})(?P=quote)""",
    re.IGNORECASE | re.VERBOSE,
)

# Values that are obviously not real secrets. A placeholder in an example, a
# variable being interpolated, a reference to configuration — flagging any of
# these is the false alarm this category cannot afford. Several entries were
# earned the hard way against real repositories: `chang` covers "changethis"
# and not just "changeme", `exampl` and `f4ke` cover leetspeak fake keys,
# `secret` covers "supersecret" (a real credential does not contain the word),
# all-digit values are mock ids, `x-oauth-basic` is GitHub's documented
# not-a-password convention, and regex metacharacters mean the "value" is a
# pattern under construction, not a credential.
_PLACEHOLDER_VALUE = re.compile(
    r"^\s*$"
    r"|chang"
    r"|your[ _-]"
    r"|my[ _-]?(?:password|secret|key)"
    r"|placeholder|exampl|sample|dummy|fake|f4ke|test|todo|secret|xxx+|\*{3,}"
    r"|^pass(?:word|wd)?[0-9]*$"
    r"|^\d+$"  # an all-digit "token" is a mock id
    r"|x-oauth-basic"
    r"|[()\[\]{}|]"  # regex fragments and interpolation syntaxes, not values
    r"|^<[^>]*>"  # <your-key-here>
    r"|[$%]\{|^\$"  # ${VAR}, $VAR — interpolation, not a value
    r"|^(?:os\.|process\.|env|getenv|config|settings)",
    re.IGNORECASE,
)


# --- Debug mode -------------------------------------------------------------

# The alternatives cover, in order: a Django settings assignment, the flag
# passed as a keyword to Flask's run call, the Flask and Laravel env-style
# spellings, and webpack's eval devtool in a production config. Comments here
# describe the patterns rather than quoting them — a quoted example is exactly
# the text being hunted, and the self-scan found one in a trailing comment on
# this very definition, which code_only keeps by design.
_DEBUG_ENABLED_PATTERN = re.compile(
    r"^\s*DEBUG\s*=\s*True\b"
    r"|debug\s*=\s*True\s*[,)]"
    r"|FLASK_DEBUG\s*=\s*1"
    r"|APP_DEBUG\s*=\s*true"
    r"|\bdevtool:\s*['\"]eval",
    re.MULTILINE,
)

# Reading the flag from the environment is the correct pattern, whatever the
# default; the environment decides, not the code.
_DEBUG_FROM_ENV = re.compile(
    r"DEBUG\s*=\s*(?:os\.|env|getenv|config|settings|process\.env)", re.IGNORECASE
)

# Files that are *for* development. Debug on in settings/dev.py is the point of
# having a settings/dev.py.
_DEV_FILE_MARKERS = ("dev", "development", "local", "debug", "override")


# --- TLS verification -------------------------------------------------------

# Every one of these is an explicit opt-out somebody typed. There is no
# accidental spelling of `verify=False`.
_TLS_DISABLED_PATTERN = re.compile(
    r"\bverify\s*=\s*False\b"  # requests / httpx
    r"|rejectUnauthorized\s*:\s*false"  # Node tls
    r"|InsecureSkipVerify\s*:\s*true"  # Go crypto/tls
    r"|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0"
    r"|ssl?[._]?_?create_unverified_context"  # Python ssl
    r"|CURLOPT_SSL_VERIFYPEER\s*(?:=>|,)\s*(?:false|0)"
)


# --- Container configuration ------------------------------------------------

# A literal secret value in a Dockerfile ENV/ARG or a Compose environment
# block. `${VAR}` pass-through is the correct pattern and never matches, since
# the placeholder guard rejects interpolation. Horizontal whitespace only
# around separators, for the same reason as the env pattern above: `\s`
# crossing a newline let Sentry's *empty* `SES_PASSWORD:` claim the following
# `SES_REGION:` line as its value. Values may be quoted; the quotes are
# stripped before the length and placeholder checks, since `"sentry"` is
# eight characters only if the quotes are miscounted as secret material.
_CONTAINER_SECRET = re.compile(
    r"""^[ \t]*(?:ENV|ARG)[ \t]+
        [A-Z_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Z_]*
        [= \t]+(?P<value>\S+)""",
    re.MULTILINE | re.IGNORECASE | re.VERBOSE,
)
# `\r?` before the anchor: repositories commit CRLF files, `\S+` rightly stops
# at the `\r`, and MULTILINE `$` only matches before `\n` — so without it every
# CRLF compose file would silently pass the check.
_COMPOSE_SECRET = re.compile(
    r"""^[ \t]+-?[ \t]*
        [A-Z_]*(?:PASSWORD|SECRET|TOKEN|API_KEY)[A-Z_]*
        [:=][ \t]*(?P<value>\S+)[ \t]*\r?$""",
    re.MULTILINE | re.VERBOSE,
)
# The shortest value worth calling a credential; shorter is a default or a tag.
_MIN_SECRET_LENGTH = 8


# --- .gitignore -------------------------------------------------------------

# Evidence the project loads configuration from env files, which is when an
# unprotected .env becomes a loaded gun rather than a hypothetical.
_ENV_FILE_USAGE = re.compile(r"\b(?:python-dotenv|load_dotenv|dotenv|django-environ|environs)\b")


def _is_dockerfile(path: Path) -> bool:
    name = path.name.lower()
    return name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile")


def _is_compose_file(path: Path) -> bool:
    name = path.name.lower()
    return ("compose" in name and name.endswith((".yml", ".yaml"))) or name in {
        "docker-compose.yml",
        "docker-compose.yaml",
    }


class SecurityScanner:
    category = CATEGORY
    CHECKS = (_CREDENTIAL_FILES, _HARDCODED, _DEBUG, _TLS, _CONTAINER, _GITIGNORE)

    def scan(self, repo: RepositoryIndex) -> list[CheckResult]:
        credential_files: list[str] = []
        secret_files: list[str] = []
        debug_files: list[str] = []
        tls_files: list[str] = []
        container_files: list[str] = []
        container_configs = 0

        for path in repo.files:
            # A credential fixture inside tests/ is a fixture. Checked with
            # is_test_file rather than repo.test_files, because that set only
            # holds *source* files — a .pem under tests/ is not in it, and the
            # skip must apply to exactly the non-code files it would miss.
            if is_test_file(path, repo.path):
                continue
            if self._is_committed_credential(repo, path):
                credential_files.append(repo.relative(path))
            if _is_dockerfile(path) or _is_compose_file(path):
                container_configs += 1
                if self._holds_container_secret(repo, path):
                    container_files.append(repo.relative(path))

        for path in repo.production_files:
            content = code_only(repo.read(path))
            if not content:
                continue
            if self._holds_hardcoded_secret(content):
                secret_files.append(repo.relative(path))
            if self._enables_debug(path, content):
                debug_files.append(repo.relative(path))
            if _TLS_DISABLED_PATTERN.search(content):
                tls_files.append(repo.relative(path))

        has_source = bool(repo.production_files)
        return [
            self._check_credential_files(credential_files),
            self._check_hardcoded_secrets(has_source, secret_files),
            self._check_debug(has_source, debug_files),
            self._check_tls(has_source, tls_files),
            self._check_container_config(container_configs, container_files),
            self._check_gitignore(repo),
        ]

    # --- detection ---------------------------------------------------------

    def _is_committed_credential(self, repo: RepositoryIndex, path: Path) -> bool:
        name = path.name.lower()
        if any(name.endswith(suffix) for suffix in _ENV_TEMPLATE_SUFFIXES):
            return False
        if name in _ALWAYS_CREDENTIAL_NAMES:
            return True
        # For everything below, the name only nominates — the content decides.
        # An env file of blanked defaults, an .npmrc interpolating a token from
        # the environment, and an OAuth client JSON with no private key are all
        # committed on purpose by well-run projects.
        if name == ".env" or name.startswith(".env."):
            return self._has_real_value(repo.read(path, max_bytes=65536), _ENV_SECRET_LINE)
        if name in _CONFIG_CREDENTIAL_NAMES:
            return self._has_real_value(repo.read(path, max_bytes=65536), _CONFIG_SECRET_LINE)
        if name in _JSON_CREDENTIAL_NAMES:
            return bool(_JSON_SECRET_FIELD.search(repo.read(path, max_bytes=65536)))
        suffix = path.suffix.lower()
        if suffix in _BINARY_KEY_EXTENSIONS:
            return True
        if suffix in _KEY_EXTENSIONS:
            # Only the file's own header can distinguish a private key from a
            # public certificate, and certificates are supposed to be committed.
            return bool(_PRIVATE_KEY_HEADER.search(repo.read(path, max_bytes=4096)))
        return False

    def _has_real_value(self, content: str, pattern: re.Pattern[str]) -> bool:
        for match in pattern.finditer(content):
            value = match.group("value").strip().strip("'\"")
            if value and not _PLACEHOLDER_VALUE.search(value):
                return True
        return False

    def _holds_hardcoded_secret(self, content: str) -> bool:
        # Even a known token format gets the placeholder check: fake keys in
        # documentation and UI examples are written *in* the real format with
        # FAKE or EXAMPLE spelled into the middle, and matched without looking.
        for match in _KNOWN_TOKEN_FORMATS.finditer(content):
            if not _PLACEHOLDER_VALUE.search(match.group()):
                return True
        for match in _SECRET_ASSIGNMENT.finditer(content):
            if not _PLACEHOLDER_VALUE.search(match.group("value")):
                return True
        return False

    def _enables_debug(self, path: Path, content: str) -> bool:
        # Debug on in a file named for development is the point of that file.
        stem = path.stem.lower()
        if any(marker in stem for marker in _DEV_FILE_MARKERS):
            return False
        if _DEBUG_FROM_ENV.search(content):
            return False
        return bool(_DEBUG_ENABLED_PATTERN.search(content))

    def _holds_container_secret(self, repo: RepositoryIndex, path: Path) -> bool:
        content = repo.read(path)
        pattern = _CONTAINER_SECRET if _is_dockerfile(path) else _COMPOSE_SECRET
        for match in pattern.finditer(content):
            value = match.group("value").strip("'\"")
            if len(value) >= _MIN_SECRET_LENGTH and not _PLACEHOLDER_VALUE.search(value):
                return True
        return False

    # --- findings ----------------------------------------------------------

    def _check_credential_files(self, found: list[str]) -> CheckResult:
        # Never skipped: any repository can commit a key, and "there was no
        # source code" is no reason not to have looked.
        if not found:
            return passed(_CREDENTIAL_FILES)
        others = f" and {len(found) - 1} other files" if len(found) > 1 else ""
        return failed(
            _CREDENTIAL_FILES,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.CRITICAL,
                title="Credential files are committed",
                description=(
                    f"{found[0]}{others} is committed to the repository. Everything in git history "
                    "stays retrievable forever — deleting the file in a later commit does not "
                    "remove it — so anything inside these files is exposed to everyone with access "
                    "to the repository, for as long as it exists anywhere."
                ),
                recommendation=(
                    "Rotate every credential these files contain — treat them as leaked, because "
                    "they are. Then remove the files, add their names to .gitignore, and commit a "
                    "blanked .env.example instead so newcomers know what to configure."
                ),
                score_impact=_CREDENTIAL_FILE,
            ),
        )

    def _check_hardcoded_secrets(self, has_source: bool, found: list[str]) -> CheckResult:
        if not has_source:
            return skipped(_HARDCODED, _NO_SOURCE)
        if not found:
            return passed(_HARDCODED)
        others = f" and {len(found) - 1} other files" if len(found) > 1 else ""
        return failed(
            _HARDCODED,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.CRITICAL,
                title="Secrets are hardcoded in source",
                description=(
                    f"{found[0]}{others} contains what looks like a real credential in the code "
                    "itself. It is readable by anyone with repository access, it survives in git "
                    "history after removal, and it ships inside every build artefact made from "
                    "this code."
                ),
                recommendation=(
                    "Rotate the credential first. Then load it from the environment or a secrets "
                    "manager, and add a pre-commit secret scanner so the next one is caught before "
                    "it lands."
                ),
                score_impact=_HARDCODED_SECRET,
            ),
        )

    def _check_debug(self, has_source: bool, found: list[str]) -> CheckResult:
        if not has_source:
            return skipped(_DEBUG, _NO_SOURCE)
        if not found:
            return passed(_DEBUG)
        others = f" and {len(found) - 1} other files" if len(found) > 1 else ""
        return failed(
            _DEBUG,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.HIGH,
                title="Debug mode is enabled",
                description=(
                    f"{found[0]}{others} enables debug mode unconditionally. Beyond leaking stack "
                    "traces and configuration to anyone who triggers an error, some debuggers are "
                    "worse: Werkzeug's interactive console is remote code execution for whoever "
                    "reaches it."
                ),
                recommendation=(
                    "Read the debug flag from the environment with the default off, so production "
                    "gets it right by doing nothing."
                ),
                score_impact=_DEBUG_ENABLED,
            ),
        )

    def _check_tls(self, has_source: bool, found: list[str]) -> CheckResult:
        if not has_source:
            return skipped(_TLS, _NO_SOURCE)
        if not found:
            return passed(_TLS)
        others = f" and {len(found) - 1} other files" if len(found) > 1 else ""
        return failed(
            _TLS,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.HIGH,
                title="TLS certificate verification is disabled",
                description=(
                    f"{found[0]}{others} turns off certificate verification for outbound "
                    "connections. Every call made this way will happily talk to an impersonator, "
                    "so anything sent — including credentials — can be read and altered in "
                    "transit."
                ),
                recommendation=(
                    "Remove the override. If an internal service uses a private CA, trust that CA "
                    "explicitly instead of trusting everyone."
                ),
                score_impact=_TLS_DISABLED,
            ),
        )

    def _check_container_config(self, config_count: int, found: list[str]) -> CheckResult:
        if config_count == 0:
            return skipped(_CONTAINER, "no Dockerfile or Compose file was found to inspect")
        if not found:
            return passed(_CONTAINER)
        others = f" and {len(found) - 1} other files" if len(found) > 1 else ""
        return failed(
            _CONTAINER,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.HIGH,
                title="Secrets are baked into container configuration",
                description=(
                    f"{found[0]}{others} sets a credential to a literal value. A value in a "
                    "Dockerfile is stored in the image layers and readable by anyone who can pull "
                    "the image; a literal in a Compose file is committed alongside the code."
                ),
                recommendation=(
                    "Pass secrets through at runtime — `${VAR}` interpolation from the "
                    "environment, an env_file kept out of git, or your platform's secret store."
                ),
                score_impact=_SECRET_IN_CONTAINER_CONFIG,
            ),
        )

    def _check_gitignore(self, repo: RepositoryIndex) -> CheckResult:
        # Only meaningful for a project that demonstrably loads env files;
        # otherwise there is nothing for the missing rule to fail to protect.
        uses_env = bool(_ENV_FILE_USAGE.search(repo.manifest_text()))
        if not uses_env:
            return skipped(_GITIGNORE, "the project does not load configuration from env files")

        gitignore = repo.read(repo.path / ".gitignore")
        if ".env" in gitignore:
            return passed(_GITIGNORE)

        return failed(
            _GITIGNORE,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title=".gitignore does not protect env files",
                description=(
                    "The project loads configuration from env files, but .gitignore has no rule "
                    "for them. Nothing stands between one absent-minded `git add .` and a "
                    "committed credential — this is the guard rail for the two findings above "
                    "this one."
                ),
                recommendation=(
                    "Add `.env` and `.env.*` to .gitignore, with `!.env.example` if a template "
                    "is committed."
                ),
                score_impact=_NO_GITIGNORE_PROTECTION,
            ),
        )
