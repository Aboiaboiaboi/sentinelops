<div align="center">

# SentinelOps

**Is this application ready for production?**

Point SentinelOps at a Git repository. It clones it, runs 29 checks across six
categories, and gives you a score out of 100 with specific findings — what's
wrong, why it matters, and what to do about it. It also tells you which commit
it looked at, what changed since the last scan, and what it *verified* rather
than only what broke.

[Quick start](#quick-start-5-minutes) ·
[What it checks](#what-it-checks) ·
[Beyond the score](#beyond-the-score) ·
[For developers](#for-developers) ·
[How it works](#how-it-works)

</div>

---

## What it does

You give it a repository URL. A few seconds later you get something like this:

Here is SentinelOps scanning **itself**, which is the real output of the
commands below rather than an illustration:

```
sentinelops                                        92 / 100    Grade A
6 of 6 categories reported
29 checks: 21 passed · 4 skipped · 2 failed · 2 not yet implemented

  Security         25 / 25   ████████████████████
  Architecture     20 / 20   ████████████████████
  Reliability      20 / 20   ████████████████████
  Scalability      10 / 10   ████████████████████
  Deployment       11 / 15   ██████████████░░░░░░
  Observability     6 / 10   ████████████░░░░░░░░
```

Each finding tells you what it found and what to do:

> **No metrics or error tracking** · MEDIUM · −4
> No metrics, tracing, or error-reporting library was found. Nothing measures
> latency, error rate or throughput, and no exception is reported anywhere — so
> the first notice that the service is broken comes from whoever is using it.
>
> **Recommendation:** Export a handful of metrics that describe user-visible
> health, and send unhandled exceptions to an error tracker so they are seen
> without being hunted.

Both of its findings are fair, and both are on the roadmap below.

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

Two more short-lived jobs run alongside them and then exit. They download the
vulnerability database and rule set that the security tools need, into a cache
volume — roughly 100 MB over the wire and about 1.1 GB on disk. The tools
themselves run with no network at all, so this is the only moment that data can
arrive. Nothing waits on it: the rest of the app is usable immediately, and
until the cache is ready the checks that depend on it report *errored* rather
than pretending your repository is clean.

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
docker compose down -v    # stop and delete the database and caches too
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

Six categories, weighted to sum to 100, and 29 individual checks:

| Category | Weight | Checks | What it looks at |
|---|---:|---:|---|
| **Security** | 25 | 8 | committed credentials, leaked secrets (**Gitleaks**), vulnerable dependencies, dangerous code patterns, debug mode, TLS overrides, container secrets, `.gitignore` |
| **Reliability** | 20 | 4 | health endpoint, request timeouts, swallowed errors, retries |
| **Architecture** | 20 | 5 | tests, dependency locking, file size, layout, documentation |
| **Deployment** | 15 | 6 | deployment config, image pinning, non-root user, healthcheck, build context, CI |
| **Observability** | 10 | 3 | logging, structured output, metrics and error tracking |
| **Scalability** | 10 | 3 | in-memory state, local file storage, connection pooling |

All six scanners are live, so a spotless repository really can score 100. A
category that fails to report **contributes nothing** — it isn't quietly
excluded from the total, so a partial scan can't masquerade as a thorough one.

The same rule catches a subtler case: a category whose every check was
*skipped* assessed nothing, so it earns nothing either. Scalability on a CLI
tool is the plain example — all three of its checks ask what a second copy
behind a load balancer would do, none of them apply, and paying it the full 10
would be marks for work nobody did. One passing check is enough to keep the
category; the rule only bites when nothing ran at all.

The security category is part tooling, part reading. **Gitleaks** answers "is a
credential committed here?" — it runs in a sandbox with no network, and secrets
are redacted from its output before SentinelOps ever sees them, so the finding
records *that* a credential is exposed and never the credential itself. Trivy
and Semgrep follow, for vulnerable dependencies and dangerous code patterns;
until they land, those two checks report **not yet implemented** rather than
passing, because a check nobody has written has not established anything.

The five regex checks that remain — credential files, debug mode, TLS overrides,
container secrets, `.gitignore` — were kept rather than routed through a tool.
They are dogfooded against real repositories, they cost nothing, and their
measured false-positive rate is zero.

### It tries hard not to cry wolf

A scanner that flags healthy code gets ignored, and then it catches nothing. So:

- **A CLI tool isn't penalised for having no health endpoint.** It shouldn't
  have one. Checks that only make sense for a web service are *skipped* for
  anything else — and a skip is reported as a skip, never quietly as a pass.
- **Test files are judged differently from production code.** A test that
  deliberately swallows an exception is fine, and a format-valid fake key in a
  fixture is a fixture. That one is measured, not assumed: run against a fresh
  clone of SentinelOps, Gitleaks reports seven leaks and all seven are fixtures
  in the security scanner's own tests. The cost is that a real credential
  committed inside a test is not reported — a trade taken deliberately, because
  a tool that flags a project's own fixtures gets muted, and a muted tool
  catches nothing.
- **Machine-generated code is excluded.** On some repositories that's 80%+ of
  the files, and "split this 4000-line generated client into modules" is not
  advice anyone can act on.
- **A library mentioned in a comment isn't a library you use.** Evidence means
  an import, a dependency, or an actual call.
- **A filename alone never convicts.** A committed `.env` where every secret is
  blank or `changethis` is a template; a `.pem` is flagged only if its own
  header says *private* key, because a public certificate is supposed to be
  committed; `${VAR}` in an `.npmrc` is interpolation done right, not a token.
- **An empty repository is not a perfect one.** With no source code there is
  nothing to assess, so it scores 0 out of 100 rather than collecting marks for
  problems nobody could find. It used to score 77.

---

## Beyond the score

A number on its own is hard to trust. Every scan also carries the context that
makes it checkable:

| | What you get | Why it exists |
|---|---|---|
| **Commit context** | The sha, message, author and date of the commit that was scanned | Turns "the score dropped 6" into "the score dropped 6 *at this commit*" |
| **What was checked** | All 29 checks with an outcome each — passed, failed, skipped with a reason, or errored when a tool could not run | A category at full marks can say *what it verified*, instead of merely having nothing to complain about |
| **Comparison** | Score and per-category movement against the previous scan, plus the exact checks that flipped | Regressions first, because what broke is what you need to see |
| **Failure diagnostics** | A category, a plain-language detail, and a suggested fix when a scan fails | A scan that just says "failed" is a dead end |

The comparison is deliberately conservative and will **decline** to show a
difference in three cases: when the scoring rules changed between the two scans
(the delta would measure a change in SentinelOps, not in your repository), when
a category stopped being assessed (that is not a regression), and for a check
added since the last scan (we moved, your repository didn't).

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
# Backend — 882 tests. Needs Postgres running. The sandbox integration tests
# skip themselves when no Docker daemon is reachable.
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .

# Frontend — 71 tests
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
> `server_default` unless you declare it on the column, so adding a `NOT NULL`
> column produces a migration that works on an empty database and fails on a
> populated one — declare it, and test the migration against real rows. And it
> won't drop Postgres `ENUM` types in a downgrade, so those need explicit
> `sa.Enum(name=...).drop()` calls; newer columns use a plain `String` with a
> Python enum for exactly this reason.

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
   `category: str`, `CHECKS: tuple[CheckSpec, ...]`, and
   `scan(repo: RepositoryIndex) -> list[CheckResult]`
2. Register it in `backend/app/scanners/registry.py`
3. Add `backend/tests/test_<category>_scanner.py`

Each check returns its own outcome, so "verified and fine" stays distinct from
"did not apply":

```python
def _check_health(self, repo: RepositoryIndex, has_health: bool) -> CheckResult:
    if not repo.is_service:
        return skipped(_HEALTH, "only asked of something that serves traffic")
    if has_health:
        return passed(_HEALTH)
    return failed(_HEALTH, ScanFinding(...))
```

Scanners are **synchronous** and receive a pre-built `RepositoryIndex` — the
tree is walked once per scan, not once per scanner. The worker dispatches them
off the event loop and pulls findings out with `findings_of()`, which is what
keeps scoring identical to before check outcomes existed.

A check backed by a tool lives in `scanners/security/tools/`, runs through
`utils/sandbox.py`, and reports `errored` whenever the tool could not run —
never `passed`. That distinction is not academic: an early version read
Gitleaks' "I could not read this directory" exit code as its "no leaks found"
exit code, and reported a repository full of credentials as clean. The tool now
runs with `--exit-code 0`, so a non-zero exit means one thing only.

Conventions: impacts sum to exactly the category weight; one finding per
*problem* rather than per file; read `production_files`; guard service-only
checks with `repo.is_service`; and return a result for **every** declared
check — a shared test asserts it against several repositories, so a check you
forget to run fails the suite rather than silently reporting as passed.

---

## How it works

```
Frontend (React)  ──►  API (FastAPI)  ──►  Postgres
                            │                 ▲
                            ▼                 │
                       Redis queue ──► Worker ┘
                                          │
                                          ▼
                  clone → index → 6 scanners → 29 checks → score
                                        │
                                        └─ sandboxed tools (no network)
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
| `backend/app/scanners/` | Pure: a repository in, check results out. No database, no API |
| `backend/app/workers/` | Queue tasks and repository cloning |
| `backend/app/models/` `schemas/` | Database tables, and API shapes — deliberately separate |
| `frontend/src/api/` `hooks/` | Fetch functions, and the query wrappers around them |
| `frontend/src/pages/` `components/` | Screens and the pieces they're built from |

Three boundaries do real work:

- **`schemas/` is not `models/`.** The `User` table has a `password_hash`; no
  response schema references it, so it cannot leak.
- **`scanners/` imports nothing from the app** except the sandbox boundary. No
  database, no queue, no HTTP, and no configuration — a tool declares *that* it
  needs the warmed cache, and the runner knows which volume that is. Which is
  why every scanner is testable against a directory in `tmp_path` and a fake
  runner.
- **`utils/storage.py`, `utils/queue.py` and `utils/sandbox.py`** are the only
  files allowed to know how work leaves the process — a bucket, a broker, or a
  container runtime. There are currently **zero** cloud SDK dependencies, so the
  containers run anywhere; swapping Redis for SQS, or Docker for Cloud Run Jobs,
  is a change in one file. `sandbox.py` defaults to refusing: with no runtime
  configured a tool check reports *errored*, never *passed*.

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
- Credentials are redacted from git's output before it reaches a log or the
  database, and a failure's stored detail is fixed text chosen by the error
  type — never the error text itself, which can echo a URL carrying a token

Passwords use bcrypt, hashed off the event loop. The auth token is an `httpOnly`
cookie rather than localStorage, since a tool that assesses other people's
security shouldn't use an XSS-readable token store. Login runs in constant time
whether or not the account exists, so it can't be used to enumerate users. Auth
endpoints are rate limited.

---

## Roadmap

- [x] **Foundation** — auth, database, API, Docker
- [x] **Scanning engine** — all 6 scanners, plus private repositories
- [x] **Explainability** — commit context on each scan, real reasons when one
      fails, which checks passed rather than only what broke, scan-to-scan
      comparison, and editing that preserves history
- [ ] **Security tooling** — real tools instead of regexes, each in its own
      sandbox behind a `SandboxRunner` boundary. Gitleaks is in and answers the
      leaked-secret check; Trivy (vulnerable dependencies) and Semgrep
      (dangerous code patterns) are next, and their checks report *not yet
      implemented* until they land. OSV was dropped as a duplicate of Trivy
- [ ] **Reporting** — PDF export
- [ ] **Production deployment** — CI/CD, load testing, and cloud hosting on
      Cloud Run

Private repositories use a GitHub App rather than stored access tokens, so
credentials expire hourly and are never persisted.

Deliberately deferred: CI for SentinelOps itself and metrics — which are, not
coincidentally, the only two findings its own scanner reports about it.

## Stack

**Frontend** — React, TypeScript, Vite, Tailwind, shadcn/ui, TanStack Query,
Recharts, Vitest

**Backend** — FastAPI, Pydantic, SQLAlchemy 2.0 (async), Alembic, PostgreSQL,
Redis + arq, bcrypt, PyJWT, slowapi, pytest

**Infrastructure** — Docker Compose, multi-stage images with separate API and
worker targets

Two dependency choices worth knowing: `bcrypt` directly rather than passlib
(unmaintained, breaks against bcrypt 4.x) and PyJWT rather than python-jose
(unmaintained, signature-verification CVEs).

## License

Apache 2.0 — see [LICENSE](LICENSE).
