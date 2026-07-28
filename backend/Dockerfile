# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: builder — resolves and installs dependencies.
#
# This stage carries uv, build caches, and anything a package needed to compile.
# None of it ships: the runtime stage below copies only the finished virtualenv,
# so the tooling used to build the image never becomes part of its attack
# surface. That is the point of a multi-stage build.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

# uv arrives as a single static binary copied from its official image. Cleaner
# than piping an install script through a shell, and the version is pinned by
# the tag rather than by whatever the script downloads today.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

# Compile .pyc files at build time so the container does not pay that cost on
# first request. copy link-mode avoids hardlink warnings when the uv cache and
# the target venv sit on different filesystem layers.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependency manifests are copied on their own, before the source. Docker caches
# each layer and invalidates everything after the first change — so if source
# were copied first, editing one line of Python would re-resolve and re-download
# every dependency. This way that only happens when the lockfile actually
# changes.
COPY pyproject.toml uv.lock ./

# --frozen fails the build if uv.lock disagrees with pyproject.toml, so an image
# can never be built from an unlocked or stale dependency set.
# --no-dev leaves pytest, ruff and friends out of the runtime image.
# The cache mount persists uv's download cache between builds without adding a
# layer to the final image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2: runtime — only what is needed to serve requests.
# ---------------------------------------------------------------------------
FROM python:3.14-slim

# A container process defaults to root. If an attacker escapes the application,
# root inside the container is a far better starting position than an
# unprivileged account — and with some misconfigurations it maps to root on the
# host. Creating a dedicated user costs one line.
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

# Only the virtualenv crosses the stage boundary. uv, the build cache, and the
# compilers are all left behind in the builder.
#
# Copied as root, deliberately. The app user needs to read and execute this, not
# write it — leaving the code owned by the account that runs it would let anyone
# who compromises the process rewrite the application and survive a restart.
COPY --from=builder /app/.venv /app/.venv

# Putting the venv's bin directory first means `uvicorn` and `alembic` resolve
# to it without activating anything — activation is a shell concept and there is
# no shell in the middle of a container's CMD.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Application code last: it changes on every commit, so it sits in the final
# layer where a rebuild is cheapest. Root-owned for the same reason as the venv.
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Everything the process writes at runtime lives here, outside the code tree.
# WORKDIR creates /app as root, so an unprivileged process cannot mkdir inside
# it — reports would fail with EACCES the first time one was generated. Giving
# writes their own directory fixes that and keeps /app read-only to the app,
# which is the stronger arrangement anyway.
RUN mkdir -p /data && chown app:app /data
ENV STORAGE_DIR=/data/reports

USER app

EXPOSE 8000

# Docker needs a way to tell "process started" from "application is serving".
# Compose uses this to gate `depends_on: condition: service_healthy`.
# Written in Python rather than curl because the slim image has no curl, and
# installing one purely for a healthcheck adds a package for no other reason.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"]

# --host 0.0.0.0 is not optional. Uvicorn defaults to 127.0.0.1, which inside a
# container means the container's own loopback — the published port would
# connect to nothing and the failure looks like a networking problem rather than
# a binding one.
#
# Exec form (a JSON array) rather than a shell string, so uvicorn runs as PID 1
# and receives SIGTERM directly. Wrapped in a shell it would not, and every stop
# would wait out the ten-second timeout before being killed.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
