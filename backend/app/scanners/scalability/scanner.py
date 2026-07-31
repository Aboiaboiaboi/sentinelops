"""Scalability checks.

One narrow question: if you ran five copies of this service behind a load
balancer, would it still behave *correctly*? Not "is it fast" — throughput is a
measurement, not something a file tree can answer. Purely whether anything here
assumes there is only ever one copy of it running.

Keeping to that question is what makes these checks defensible. "No caching
layer" was considered and cut: the only detectable signal is the absence of
Redis from the manifest, and plenty of correct services need no cache, so it
would fire on healthy repositories and teach people to ignore the category.

Every check is gated on `repo.is_service`. A CLI tool or a library has no copies
to be inconsistent across, so none of this applies to one.
"""

import re

from app.scanners.base import RepositoryIndex, ScanFinding, Severity, code_only

CATEGORY = "scalability"

# Impacts, summing to the category weight of 10. In-memory state is worth more
# than the other two together because it is the only one that makes a second
# copy actively wrong rather than merely wasteful.
_IN_MEMORY_STATE = 4
_LOCAL_FILE_STORAGE = 3
_CONNECTION_PER_REQUEST = 3


# --- Session state ---------------------------------------------------------
#
# Deliberately not "does this app use sessions". Flask's default session is a
# signed cookie and Django's is database-backed — both survive several copies
# perfectly well, and flagging them would be a false positive on two of the
# most common stacks there are. Express is the one whose default is genuinely
# broken: `express-session` with no store keeps sessions in process memory and
# says so in its own production warning.
_UNBACKED_SESSIONS = re.compile(r"\bexpress-session\b|\bMemoryStore\b")

# Anything that moves session state out of the process.
_SESSION_STORE = re.compile(
    r"@quixo3/prisma-session-store"
    r"|\b(?:"
    r"connect-redis|connect-mongo|connect-mongodb-session|connect-pg-simple"
    r"|connect-session-sequelize|connect-session-knex|session-file-store"
    r"|connect-memcached|connect-dynamodb"
    r")\b"
)

# A module-level collection that starts empty is state something fills at
# runtime. A populated literal is a constant and is left alone — the difference
# between `_cache = {}` and `WEIGHTS = {"security": 25}` is the whole check.
_EMPTY_MODULE_STATE = re.compile(
    r"^(?!\s)(?:export\s+)?(?:const|let|var)?\s*"
    r"[A-Za-z_$][\w$]*\s*(?::\s*[^=\n]+?)?\s*=\s*"
    r"(?:\{\}|\[\]|set\(\)|dict\(\)|list\(\)|new\s+Map\(\)|new\s+Set\(\))\s*;?\s*$",
    re.MULTILINE,
)

# Whether a file handles requests, so module-level state in it is per-instance
# request state rather than a lookup table.
_ROUTE_DEFINITION = re.compile(
    r"@(?:app|router|blueprint|bp|api)\.(?:route|get|post|put|patch|delete|websocket)\b"
    r"|\b(?:app|router)\.(?:get|post|put|patch|delete|use)\s*\("
    r"|@(?:Get|Post|Put|Patch|Delete|Controller|RequestMapping|GetMapping|PostMapping)\s*\("
)


# --- File storage ----------------------------------------------------------

# Uploads written to the instance's own disk. Narrow on purpose: a generic
# `open(path, "w")` is far more often a log, a cache or a build artefact than
# user data, and flagging those would be noise.
_LOCAL_UPLOAD_TARGET = re.compile(
    r"\b(?:UPLOAD_FOLDER|UPLOAD_DIR|UPLOAD_PATH|MEDIA_ROOT|uploadDir|diskStorage)\b"
    r"|multer\s*\(\s*\{[^}]{0,200}?\bdest\b"
)

# If the project already talks to object storage it has solved this, and a local
# write is a staging step rather than the durable copy. Strongest guard here.
#
# Scoped npm packages are matched before the word-boundary group, never inside
# it: `\b@azure/...` can never match, because a boundary requires a word
# character on one side and `@` is not one. Every modern Node storage SDK is
# scoped, so folding them into the `\b(?:...)` alternation silently matched
# none of them — and the guard failing open means flagging projects that had
# already solved this properly.
_OBJECT_STORAGE = re.compile(
    r"@(?:aws-sdk|google-cloud|azure)/[\w-]*(?:s3|storage|blob)[\w-]*"
    r"|\b(?:"
    r"boto3|botocore|aws-sdk|S3Client|s3_client|minio"
    r"|google-cloud-storage|azure-storage-blob"
    r"|cloudinary|django-storages|storages\.backends|active_storage"
    r"|carrierwave|shrine|paperclip"
    r")\b",
    re.IGNORECASE,
)

# Somewhere local files are *supposed* to go.
_EPHEMERAL_PATH = re.compile(r"\b(?:tmp|temp|tempfile|cache|scratch)\b", re.IGNORECASE)


# --- Database connections --------------------------------------------------

# Only ever flags pooling being switched *off*. SQLAlchemy, Django, Rails and
# most ORMs pool by default, so the absence of pool configuration is correct and
# is never reported — the opposite would fire on almost every healthy repo.
#
# Written as the assignment it really is, rather than a bare `\bNullPool\b`.
# The loose form matched this scanner's own pattern definitions during the
# self-scan and reported SentinelOps as having unpooled connections — the same
# mistake the observability scanner made in the other direction, where prose
# about a library counted as using it.
_POOLING_DISABLED = re.compile(
    r"poolclass\s*=\s*(?:\w+\.)?NullPool"
    r"|\bpool_size\s*=\s*0\b"
    r"|\bpoolSize\s*:\s*0\b"
)

# Migration and one-shot tooling, where disabling the pool is not merely
# acceptable but correct — the script connects, does its work and exits, so a
# pool would hold connections open for nothing. Alembic's own generated env.py
# sets `poolclass=pool.NullPool`, which this check flagged on SentinelOps itself
# until the guard existed.
_MIGRATION_DIRECTORIES = frozenset({"alembic", "migrations", "migrate", "migration", "db"})


# Opening a raw driver connection inside request-handling code: a TCP handshake
# and an authentication round trip per request, and a straight line to
# exhausting the database's connection limit under load.
_RAW_CONNECT = re.compile(
    r"\b(?:psycopg2|psycopg|pymysql|MySQLdb|mariadb|cx_Oracle|oracledb)\.connect\s*\("
    r"|\bmysql\.createConnection\s*\("
    r"|\bnew\s+(?:pg\.)?Client\s*\("
)


class ScalabilityScanner:
    category = CATEGORY

    def scan(self, repo: RepositoryIndex) -> list[ScanFinding]:
        # Nothing in this category applies to something that does not serve
        # traffic. A library has no instances to be inconsistent across.
        if not repo.is_service:
            return []

        manifests = repo.manifest_text()

        stateful_routes: list[str] = []
        local_uploads: list[str] = []
        unpooled: list[str] = []
        uses_object_storage = bool(_OBJECT_STORAGE.search(manifests))

        for path in repo.production_files:
            content = code_only(repo.read(path))
            if not content:
                continue

            if not uses_object_storage and _OBJECT_STORAGE.search(content):
                uses_object_storage = True

            handles_requests = bool(_ROUTE_DEFINITION.search(content))

            if handles_requests and _EMPTY_MODULE_STATE.search(content):
                stateful_routes.append(repo.relative(path))

            if self._writes_uploads_locally(content):
                local_uploads.append(repo.relative(path))

            is_migration = self._is_migration(repo, path)
            if (_POOLING_DISABLED.search(content) and not is_migration) or (
                handles_requests and _RAW_CONNECT.search(content)
            ):
                unpooled.append(repo.relative(path))

        findings: list[ScanFinding] = []
        findings.extend(self._check_state(repo, manifests, stateful_routes))
        findings.extend(self._check_storage(local_uploads, uses_object_storage))
        findings.extend(self._check_connections(unpooled))
        return findings

    def _is_migration(self, repo: RepositoryIndex, path) -> bool:
        parts = path.relative_to(repo.path).parts[:-1]
        return any(part.lower() in _MIGRATION_DIRECTORIES for part in parts)

    def _writes_uploads_locally(self, content: str) -> bool:
        match = _LOCAL_UPLOAD_TARGET.search(content)
        if not match:
            return False
        # A destination under tmp/ or cache/ is meant to be per-instance and
        # thrown away, which is the correct use of local disk rather than a
        # problem with it.
        line_start = content.rfind("\n", 0, match.start()) + 1
        line_end = content.find("\n", match.end())
        line = content[line_start : line_end if line_end != -1 else len(content)]
        return not _EPHEMERAL_PATH.search(line)

    def _check_state(
        self, repo: RepositoryIndex, manifests: str, stateful_routes: list[str]
    ) -> list[ScanFinding]:
        unbacked_sessions = bool(
            _UNBACKED_SESSIONS.search(manifests)
        ) and not _SESSION_STORE.search(manifests)
        if not unbacked_sessions and not stateful_routes:
            return []

        if unbacked_sessions:
            detail = (
                "Session middleware is configured with no external store, so sessions are held in "
                "the memory of whichever instance created them."
            )
        else:
            others = (
                f" and {len(stateful_routes) - 1} other files" if len(stateful_routes) > 1 else ""
            )
            detail = (
                f"{stateful_routes[0]}{others} keeps mutable state in a module-level collection "
                "that request handlers write to, so each instance accumulates its own copy."
            )

        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.HIGH,
                title="State is kept in process memory",
                description=(
                    f"{detail} With more than one instance running, a user's next request can "
                    "land on a different one and find none of it — so behaviour depends on which "
                    "copy answered, and a restart loses whatever that copy was holding."
                ),
                recommendation=(
                    "Move shared state out of the process — Redis or the database — so every "
                    "instance reads the same thing and any of them can serve any request."
                ),
                score_impact=_IN_MEMORY_STATE,
            )
        ]

    def _check_storage(
        self, local_uploads: list[str], uses_object_storage: bool
    ) -> list[ScanFinding]:
        if not local_uploads or uses_object_storage:
            return []
        others = f" and {len(local_uploads) - 1} other files" if len(local_uploads) > 1 else ""
        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title="Uploaded files are written to local disk",
                description=(
                    f"{local_uploads[0]}{others} stores uploads on the instance's own filesystem, "
                    "and no object storage client was found. A file written by one instance does "
                    "not exist for the others, and a container's disk does not survive a restart "
                    "or a redeploy."
                ),
                recommendation=(
                    "Write uploads to object storage — S3, GCS or equivalent — and keep only the "
                    "key in the database, so any instance can serve any file."
                ),
                score_impact=_LOCAL_FILE_STORAGE,
            )
        ]

    def _check_connections(self, unpooled: list[str]) -> list[ScanFinding]:
        if not unpooled:
            return []
        others = f" and {len(unpooled) - 1} other files" if len(unpooled) > 1 else ""
        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title="Database connections are not pooled",
                description=(
                    f"{unpooled[0]}{others} either disables connection pooling or opens a "
                    "connection directly inside request-handling code. Every request then pays a "
                    "TCP handshake and an authentication round trip, and under load the instances "
                    "together exhaust the database's connection limit — at which point it starts "
                    "refusing everyone, not just the newest request."
                ),
                recommendation=(
                    "Create one pooled engine or client at startup and share it across requests. "
                    "Past a few dozen instances, put a connection pooler such as PgBouncer in "
                    "front of the database as well."
                ),
                score_impact=_CONNECTION_PER_REQUEST,
            )
        ]
