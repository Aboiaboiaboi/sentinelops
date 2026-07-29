# SentinelOps

**Is this application ready for production?**

Point SentinelOps at a Git repository and it clones it, inspects it across six
categories, and returns a score out of 100 with specific, actionable findings —
what is wrong, why it matters, and what to do about it.

It reads code and configuration. It does not run the repository, deploy
anything, or change it.

---

## What it does today

Submit a repository and the platform:

1. Creates a scan and returns immediately — the API never blocks on the work.
2. A background worker picks the job up, clones the repository shallowly under
   strict resource limits, and detects what the project is built with.
3. Each scanner category runs independently and reports its own result.
4. Findings are scored against category weights and written back.
5. The UI polls until the scan reaches a terminal state, then shows the score,
   a category breakdown, and every finding.

A real example — scanning [pallets/click](https://github.com/pallets/click):

```
repository cloned   2.1 MB, 194 files
framework detected  Python
category scanned    architecture — 1 finding
scan completed      score 17/100, 1 of 6 categories reported
```

> **Source files are too large to review safely** · MEDIUM · −3
> `src/click/core.py` is 3792 lines and 16 other files. A file this size is
> difficult to review, test in isolation, or change without touching unrelated
> behaviour.

### Scoring

Six categories, weighted to sum to 100:

| Category | Weight | Scanner |
|---|---:|---|
| Security | 25 | not yet built |
| Reliability | 20 | not yet built |
| Architecture | 20 | ✅ tests, lockfiles, file size, layout, docs |
| Deployment | 15 | not yet built |
| Observability | 10 | not yet built |
| Scalability | 10 | not yet built |

A category scores its weight minus the impact of its findings, floored at zero.
A category that did not report contributes **nothing** — the denominator is not
reduced. A scan that assessed a third of the rubric should not look like a
thorough one.

That is why scores are currently low: with only the architecture scanner built,
the other 80 points are honestly unassessed.

### What is not built yet

- The other five scanners
- Security tooling (Gitleaks, Trivy, Semgrep, OSV Scanner)
- Per-scanner container sandboxing, which lands with those tools since that is
  when third-party binaries start running against untrusted repositories
- Private repositories (GitHub App)
- PDF report export

---

## Running it locally

You need **Docker** and **Node 20+**. Everything else runs in containers.

### 1. Start the backend

```bash
docker compose up -d
```

That brings up five things: Postgres, Redis, a one-shot migration job, the API
on port 8000, and the background worker. Check they are up:

```bash
docker compose ps
curl localhost:8000/health
```

The migration job runs to completion *before* the API starts, so a failed
migration stops the API coming up rather than letting it serve against a schema
it does not match.

### 2. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173, create an account, add a repository, and click
**Run scan**.

The dev server proxies `/api` to the backend and strips the prefix, which keeps
the browser on one origin — the auth cookie is `httpOnly` and same-origin, so it
is never readable from JavaScript.

### Running the frontend without a backend

Every screen works against an in-memory fixture store, including the polling
loop and the three-state category chart:

```bash
cd frontend
echo "VITE_USE_FIXTURES=true" > .env.local
npm run dev
```

Set it to `false` (or delete the file) to talk to the real API again.

### Stopping

```bash
docker compose down        # keeps your data
docker compose down -v     # also drops the database and queue
```

---

## Development

### Backend

Uses [uv](https://docs.astral.sh/uv/). Postgres must be running for the tests —
they use a real database, because the schema relies on JSONB, native enums and
`ON DELETE CASCADE`, none of which SQLite reproduces faithfully.

```bash
cd backend
uv sync
uv run pytest                                    # 263 tests
uv run ruff check . && uv run ruff format --check .
uv run alembic upgrade head
uv run uvicorn app.main:app --reload             # API on :8000
uv run python -m app.workers.main                # worker
```

Copy `.env.example` to `.env` to change any settings. Every value has a
development default, so it starts with no `.env` at all.

### Frontend

```bash
cd frontend
npm run typecheck && npm run lint && npm run test && npm run build
```

### Watching a scan

```bash
docker compose logs -f worker
```

Logs are structured JSON throughout, so you can filter them:

```bash
docker compose logs worker | grep '"category scanned"'
```

---

## How it is put together

```
Frontend (React)  ──►  API (FastAPI)  ──►  Postgres
                            │                 ▲
                            ▼                 │
                       Redis queue ──► Worker ┘
                                          │
                                          ▼
                                    clone + scanners
```

The API only ever creates a scan row and enqueues a job. All the slow work —
cloning, scanning, scoring — happens in the worker, so the endpoint answers in
milliseconds no matter how large the repository is.

| Path | Contents |
|---|---|
| `backend/app/api/` | HTTP layer. Thin — calls `services/`, never the ORM directly. |
| `backend/app/services/` | Business logic, shared by the API and the worker. |
| `backend/app/scanners/` | Pure: a path in, findings out. No database, no API. |
| `backend/app/workers/` | Queue tasks, repository cloning. |
| `backend/app/models/` `schemas/` | ORM tables, and the API shapes — deliberately separate. |
| `frontend/src/api/` `hooks/` | Fetch functions, and the query wrappers around them. |
| `frontend/src/pages/` `components/` | Screens, and the pieces they are built from. |

Two boundaries are load-bearing:

- **`schemas/` is not `models/`.** The `User` table has a `password_hash`; no
  response schema references it, so it cannot leak.
- **`utils/storage.py` and `utils/queue.py`** are the only files permitted to
  know about a cloud SDK. Swapping Redis for SQS is a change in one file.

### Security notes

Repositories are treated as hostile input throughout, because they are:

- Shallow clone, no submodules, no history, no LFS objects
- Hard timeout, byte and file-count caps
- `ext::` URLs blocked — they hand git a shell command to run
- Hooks disabled, host git configuration ignored
- Symlinks are never followed, so a link to `/etc` reads nothing
- Every clone is deleted afterwards, whether the scan succeeded or not
- The worker runs as a non-root user, and application code is read-only to it
- Only the worker image contains `git` — the API cannot clone anything

Passwords use bcrypt, hashed off the event loop. The auth JWT is an `httpOnly`
cookie rather than localStorage, since a tool that assesses other people's
security should not use an XSS-readable token store. Login runs in constant time
whether or not the account exists, so it cannot be used to enumerate users.

---

## Stack

**Frontend** — React, TypeScript, Vite, Tailwind v4, shadcn/ui, TanStack Query,
Recharts, Vitest

**Backend** — FastAPI, Pydantic, SQLAlchemy 2.0 (async), Alembic, PostgreSQL,
Redis + arq, bcrypt, PyJWT, slowapi, pytest

**Infrastructure** — Docker Compose locally; multi-stage images with separate
API and worker targets

## License

Apache 2.0 — see [LICENSE](LICENSE).
