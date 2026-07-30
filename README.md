<div align="center">

# SentinelOps

**Is this application ready for production?**

Point SentinelOps at a Git repository. It clones it, inspects it across six
categories, and gives you a score out of 100 with specific findings — what's
wrong, why it matters, and what to do about it.

[Quick start](#quick-start-5-minutes) ·
[What it checks](#what-it-checks) ·
[For developers](#for-developers) ·
[How it works](#how-it-works)

</div>

---

## What it does

You give it a repository URL. A few seconds later you get something like this:

```
sentinelops-api                                    41 / 100    Grade F
4 of 6 categories reported

  Architecture     17 / 20   ████████████████░░░░
  Reliability      16 / 20   ████████████████░░░░
  Deployment        4 / 15   ████░░░░░░░░░░░░░░░░
  Observability     4 / 10   ████████░░░░░░░░░░░░
  Security          not assessed
  Scalability       not assessed
```

Each finding tells you what it found and what to do:

> **No deployment configuration** · HIGH · −11
> No Dockerfile, Compose file, or Kubernetes manifest was found. Nothing in the
> repository describes how the service is packaged or run, so how it reaches an
> environment lives only in somebody's shell history.
>
> **Recommendation:** Add a Dockerfile describing how the service is built and
> started, so the same artefact runs locally and in every environment.

It **reads** code and configuration. It never runs the repository, deploys
anything, or changes it.

---

## Quick start (5 minutes)

You need two things installed:

- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** — must be *running*, not just installed
- **[Node.js 20 or newer](https://nodejs.org/)**

### 1. Get the code

```bash
git clone https://github.com/Aboiaboiaboi/sentinelops
cd sentinelops
```

### 2. Start the backend

```bash
docker compose up -d
```

The first run takes a few minutes while it downloads and builds. It starts five
things: a database, a queue, a one-off database setup job, the API, and the
background worker that does the scanning.

Check it's alive:

```bash
curl localhost:8000/health
```

You should see `{"status":"ok"}`. If you'd rather look than type, open
<http://localhost:8000/docs> for the interactive API documentation.

### 3. Start the app

```bash
cd frontend
npm install
npm run dev
```

### 4. Use it

Open **<http://localhost:5173>**, create an account (it's local — nothing leaves
your machine), add a repository URL, and click **Run scan**.

Try `https://github.com/pallets/click` if you want something to test with.

### When you're done

```bash
docker compose down       # stop, keep your data
docker compose down -v    # stop and delete the database too
```

<details>
<summary><b>Something went wrong?</b></summary>

**`docker compose up` fails immediately** — Docker Desktop probably isn't
running. Start it and wait for the whale icon to stop animating.

**Port already in use** — something else is on 8000, 5173, 5432 or 6379. Stop
it, or change the port mapping in `docker-compose.yml`.

**The page loads but nothing works** — check the API is up with
`curl localhost:8000/health`. If not, `docker compose logs backend`.

**A scan stays "Queued" forever** — the worker isn't running.
`docker compose ps` should show `worker` as healthy; `docker compose logs worker`
will say why if it isn't.

**Want to see the app without a backend at all?**

```bash
cd frontend
echo "VITE_USE_FIXTURES=true" > .env.local
npm run dev
```

Every screen works against built-in sample data, including the live-updating
scan progress. Set it back to `false` to use the real thing.

</details>

---

## What it checks

Six categories, weighted to sum to 100:

| Category | Weight | Status | What it looks at |
|---|---:|:---:|---|
| **Security** | 25 | ⬜ planned | secrets, dependency vulnerabilities |
| **Reliability** | 20 | ✅ | health endpoint, request timeouts, retries, swallowed errors |
| **Architecture** | 20 | ✅ | tests, dependency locking, file size, layout, documentation |
| **Deployment** | 15 | ✅ | Dockerfile, image pinning, non-root user, healthcheck, CI |
| **Observability** | 10 | ✅ | logging, structured output, metrics and error tracking |
| **Scalability** | 10 | ⬜ planned | statelessness, caching, connection pooling |

### Why scores look low right now

A category that has no scanner yet **contributes nothing** — it isn't quietly
excluded from the total. A scan that assessed a third of the rubric shouldn't
look like a thorough one.

Two categories are still unbuilt, so **the current maximum is 65 / 100**. That
number goes up as scanners land, not because the checks got easier.

### It tries hard not to cry wolf

A scanner that flags healthy code gets ignored, and then it catches nothing. So:

- **A CLI tool isn't penalised for having no health endpoint.** It shouldn't
  have one. Checks that only make sense for a web service only run for one.
- **Test files are judged differently from production code.** A test that
  deliberately swallows an exception is fine.
- **Machine-generated code is excluded.** On some repositories that's 80%+ of
  the files, and "split this 4000-line generated client into modules" is not
  advice anyone can act on.
- **A library mentioned in a comment isn't a library you use.** Evidence means
  an import, a dependency, or an actual call.

---

## For developers

### Prerequisites

| Tool | Version | Why |
|---|---|---|
| [Docker](https://www.docker.com/products/docker-desktop/) | any recent | Postgres, Redis, and the app itself |
| [uv](https://docs.astral.sh/uv/) | 0.11+ | Python dependency management |
| [Node.js](https://nodejs.org/) | 20+ | Frontend |
| Python | 3.14 | Installed automatically by `uv` |

### Reproducing the dev environment

```bash
# Infrastructure only — leave the app to run on your host for fast reloads
docker compose up -d postgres redis
```

**Backend:**

```bash
cd backend
uv sync                                  # creates .venv, installs everything
cp .env.example .env                     # optional — every value has a default
uv run alembic upgrade head              # set up the database schema
uv run uvicorn app.main:app --reload     # API on :8000, reloads on save
```

In a second terminal, the worker:

```bash
cd backend
uv run python -m app.workers.main
```

> Use `python -m app.workers.main`, **not** the `arq` CLI. The CLI applies its
> own logging config after importing settings, which leaks plain-text lines into
> an otherwise JSON log stream.

**Frontend:**

```bash
cd frontend
npm install
npm run dev                              # :5173, proxies /api to :8000
```

The dev server proxies `/api` to the backend and strips the prefix, keeping the
browser on one origin — the auth cookie is `httpOnly` and same-origin, so it's
never readable from JavaScript.

### Running the checks

```bash
# Backend — 405 tests. Needs Postgres running.
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .

# Frontend — 58 tests
cd frontend
npm run typecheck && npm run lint && npm run test && npm run build
```

Tests run against a **real Postgres**, not SQLite. The schema uses JSONB, native
enums and `ON DELETE CASCADE`, none of which SQLite reproduces faithfully — a
SQLite suite would pass while the real database rejected the same code. A
disposable `sentinelops_test` database is created and dropped per run.

### Database migrations

```bash
uv run alembic upgrade head                          # apply
uv run alembic revision --autogenerate -m "message"  # create
uv run alembic downgrade -1                          # undo one
```

> **Two things Alembic won't do for you.** It never autogenerates
> `server_default`, so adding a `NOT NULL` column produces a migration that
> works on an empty database and fails on a populated one — always add one by
> hand and test against real rows. And it won't drop Postgres `ENUM` types in a
> downgrade, so those need explicit `sa.Enum(name=...).drop()` calls.

### Watching a scan

```bash
docker compose logs -f worker
```

Logs are structured JSON throughout, so they're filterable:

```bash
docker compose logs worker | grep '"category scanned"'
```

### Adding a scanner

Three steps:

1. Create `backend/app/scanners/<category>/scanner.py` with a class exposing
   `category: str` and `scan(repo: RepositoryIndex) -> list[ScanFinding]`
2. Register it in `backend/app/scanners/registry.py`
3. Add `backend/tests/test_<category>_scanner.py`

Scanners are **synchronous** and receive a pre-built `RepositoryIndex` — the
tree is walked once per scan, not once per scanner. The worker dispatches them
off the event loop.

Conventions: impacts sum to exactly the category weight; one finding per
*problem* rather than per file; read `production_files`; guard service-only
checks with `repo.is_service`.

---

## How it works

```
Frontend (React)  ──►  API (FastAPI)  ──►  Postgres
                            │                 ▲
                            ▼                 │
                       Redis queue ──► Worker ┘
                                          │
                                          ▼
                            clone → index → scanners → score
```

The API only creates a scan record and queues a job — it never does the slow
work, so it answers in milliseconds no matter how big the repository is. The
worker picks the job up, and the UI polls until the scan reaches a final state.

Each category runs independently. If one fails, the scan still completes with
the others, and the failed category costs its weight rather than silently
disappearing.

### Project layout

| Path | Contents |
|---|---|
| `backend/app/api/` | HTTP layer. Thin — calls `services/`, never the ORM directly |
| `backend/app/services/` | Business logic, shared by the API and the worker |
| `backend/app/scanners/` | Pure: a repository in, findings out. No database, no API |
| `backend/app/workers/` | Queue tasks and repository cloning |
| `backend/app/models/` `schemas/` | Database tables, and API shapes — deliberately separate |
| `frontend/src/api/` `hooks/` | Fetch functions, and the query wrappers around them |
| `frontend/src/pages/` `components/` | Screens and the pieces they're built from |

Two boundaries do real work:

- **`schemas/` is not `models/`.** The `User` table has a `password_hash`; no
  response schema references it, so it cannot leak.
- **`utils/storage.py` and `utils/queue.py`** are the only files allowed to know
  about a cloud SDK. Swapping Redis for SQS is a change in one file.

### Security

Repositories are treated as hostile input, because they are:

- Shallow clone, no submodules, no history, no LFS objects
- Hard timeout, byte and file-count caps
- `ext::` URLs blocked — they hand git a shell command to run
- Git hooks disabled, host git configuration ignored
- Symlinks are never followed, so a link to `/etc` reads nothing
- Every clone is deleted afterwards, whether the scan succeeded or not
- The worker runs as a non-root user, with application code read-only to it
- Only the worker image contains `git` — the API cannot clone anything

Passwords use bcrypt, hashed off the event loop. The auth token is an `httpOnly`
cookie rather than localStorage, since a tool that assesses other people's
security shouldn't use an XSS-readable token store. Login runs in constant time
whether or not the account exists, so it can't be used to enumerate users. Auth
endpoints are rate limited.

---

## Roadmap

- [x] **Foundation** — auth, database, API, Docker
- [ ] **Scanning engine** — 4 of 6 scanners built; scalability, baseline
      security, and private repositories remain
- [ ] **Security tooling** — Gitleaks, Trivy, Semgrep, OSV, each in its own
      sandbox
- [ ] **Reporting** — PDF export
- [ ] **Production deployment** — CI/CD and cloud hosting

Private repositories will use a GitHub App rather than stored access tokens, so
credentials expire hourly and are never persisted.

## Stack

**Frontend** — React, TypeScript, Vite, Tailwind, shadcn/ui, TanStack Query,
Recharts, Vitest

**Backend** — FastAPI, Pydantic, SQLAlchemy 2.0 (async), Alembic, PostgreSQL,
Redis + arq, bcrypt, PyJWT, slowapi, pytest

**Infrastructure** — Docker Compose, multi-stage images with separate API and
worker targets

## License

Apache 2.0 — see [LICENSE](LICENSE).
