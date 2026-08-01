<div align="center">

# SentinelOps

**Is this application ready for production?**

Point SentinelOps at a Git repository. It clones it, runs 27 checks across six
categories, and gives you a score out of 100 — with specific findings, the
commit it looked at, what changed since last time, and what it verified rather
than only what broke.

[Quick start](#quick-start-5-minutes) ·
[What it checks](#what-it-checks) ·
[Features](#features-and-why-they-exist) ·
[For developers](#for-developers) ·
[How it works](#how-it-works)

</div>

---

## What it does

Here is SentinelOps scanning **itself** — the real output of a clone-and-scan,
not an illustration:

```
sentinelops                                        92 / 100    Grade A
6 of 6 categories reported     27 checks: 21 passed · 4 skipped · 2 failed

  Security         25 / 25   ████████████████████
  Architecture     20 / 20   ████████████████████
  Reliability      20 / 20   ████████████████████
  Scalability      10 / 10   ████████████████████
  Deployment       11 / 15   ██████████████░░░░░░
  Observability     6 / 10   ████████████░░░░░░░░
```

Every finding says what it found, why it matters, and what to do:

> **No metrics or error tracking** · MEDIUM · −4
> No metrics, tracing, or error-reporting library was found. Nothing measures
> latency, error rate or throughput, and no exception is reported anywhere — so
> the first notice that the service is broken comes from whoever is using it.
>
> **Recommendation:** Export a handful of metrics that describe user-visible
> health, and send unhandled exceptions to an error tracker so they are seen
> without being hunted.

Both of its own findings are fair, and both are on the roadmap below.

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

Six categories, weighted to sum to 100, and 27 individual checks:

| Category | Weight | Checks | What it looks at |
|---|---:|---:|---|
| **Security** | 25 | 6 | committed credentials, hardcoded secrets, debug mode, TLS verification, container secrets, `.gitignore` |
| **Reliability** | 20 | 4 | health endpoint, call timeouts, swallowed errors, retries |
| **Architecture** | 20 | 5 | tests, dependency locking, file size, module layout, README |
| **Deployment** | 15 | 6 | deployment config, image pinning, non-root, healthcheck, build context, CI |
| **Observability** | 10 | 3 | logging, structured output, metrics and error tracking |
| **Scalability** | 10 | 3 | in-memory state, local file storage, connection pooling |

A repository with nothing wrong scores 100. The security category is a
deliberately shallow baseline for now — dedicated tools (Gitleaks, Trivy,
Semgrep, OSV) replace it in a later phase, each sandboxed.

### It tries hard not to cry wolf

A scanner that flags healthy code gets ignored, and then it catches nothing. So:

- **A CLI tool isn't penalised for having no health endpoint.** It shouldn't
  have one. Checks that only make sense for a web service are *skipped* for
  everything else — and skipped is reported as skipped, never as passed.
- **Test files are judged differently from production code.** A test that
  deliberately swallows an exception is fine.
- **Machine-generated code is excluded.** On some repositories that's 80%+ of
  the files, and "split this 4000-line generated client into modules" is not
  advice anyone can act on.
- **A library mentioned in a comment isn't a library you use.** Evidence means
  an import, a dependency, or an actual call — never prose.
- **A filename alone never convicts.** A committed `.env` where every secret is
  blank or `changethis` is a template; a `.pem` is flagged only if its own
  header says *private key*, because a public certificate is meant to be
  committed.

---

## Features, and why they exist

Every feature below exists because of a specific way a naive version would have
lied to you. The code excerpts are the real thing, trimmed.

### 1. A score that can't be gamed by silence

**The problem.** Each scanner is built to stay quiet rather than guess. Summed
up, that meant an *empty repository* scored **77/100** — it beat a real Flask
app, because nothing could be found wrong with nothing.

**What we do.** A category that assessed nothing contributes nothing, and the
denominator never shrinks:

```python
# workers/scan_tasks.py — a repository with no source is not an application
if not index.source_files:
    for category in SCAN_CATEGORIES:
        await record(category, CategoryStatus.FAILED)
    return findings, category_status, checks

# ...and a category whose every check skipped assessed nothing either
if results and all(result.outcome is CheckOutcome.SKIPPED for result in results):
    await record(category, CategoryStatus.FAILED)
```

**Why it matters.** An empty repo now scores **0**, and the UI says *"0 of 6
categories reported"* so the zero is explained rather than accusatory. One
passing check is enough to keep a category — the rule only bites when nothing
ran at all.

### 2. Check-level results, not just failures

**The problem.** Scanners returned findings — problems only. So *"this service
has a health endpoint"* and *"this is a CLI tool, the question doesn't apply"*
were both an empty list. A category showing full marks couldn't say whether it
had verified anything.

**What we do.** Every check reports its own outcome:

```python
# scanners/reliability/scanner.py
def _check_health(self, repo: RepositoryIndex, has_health: bool) -> CheckResult:
    if not repo.is_service:
        return skipped(_HEALTH, "only asked of something that serves traffic")
    if has_health:
        return passed(_HEALTH)
    return failed(_HEALTH, ScanFinding(...))
```

**Why this design.** The cheaper alternative — declare the checks and infer
passes by subtraction — was rejected: forgetting one line would silently report
a check as **passed**, which is exactly the class of lie this project keeps
hunting. Here a missed check is a type error, and a shared test asserts every
scanner accounts for every check it declares, on every repository.

### 3. Commit context

```python
# workers/repo.py — free on a shallow clone
_COMMIT_FORMAT = "%H%x00%an%x00%aI%x00%s"
```

**Why.** *"The score dropped 6"* is much more useful as *"the score dropped 6
**at this commit**"*. Recorded before the scanners run, so a scan that fails
partway still says what it was looking at.

### 4. Real reasons when a scan fails

**The problem.** A failed scan said *"failed"* and nothing else — a dead end.

**What we do.** Seven categories, each with a stored detail and a hint derived
from the category. The subtle part is where the detail comes from:

```python
# workers/scan_tasks.py — stderr is READ to classify, and never stored
haystack = str(error).lower()
for signature, category in _CLONE_FAILURE_SIGNATURES:
    if signature in haystack:
        return category, _ERROR_DETAILS[category]   # fixed text, not stderr
```

**Why.** git's stderr can echo the clone URL, and for a private repository that
URL carries an installation token. Credentials are redacted before the text
goes anywhere — including the log — and the *stored* detail is fixed text chosen
by the match, never the text that produced it.

### 5. Scan-to-scan comparison

**What you see.** `+36` with the categories that moved and the exact checks
that flipped, regressions first.

**The interesting part is what it refuses to do:**

```python
# services/comparison_service.py
if previous.scoring_version != current.scoring_version:
    return ScanComparison(comparable=False, reason=(
        "These scans were scored under different rubrics ... so the difference "
        "between them would measure a change in SentinelOps rather than in the "
        "repository."), ...)
```

Three refusals in total: a rubric change declines the delta; a category that
stopped being assessed reports `null` rather than minus its weight; and a check
*we* added since the last scan isn't counted as a change, because the repository
didn't move — we did.

### 6. Private repositories, without storing credentials

```python
# services/github_app_service.py
JWT_BACKDATE_SECONDS = 60      # absorbs clock drift vs GitHub
JWT_LIFETIME_SECONDS = 9 * 60  # GitHub rejects anything over ten minutes
TOKEN_SAFETY_MARGIN_SECONDS = 5 * 60
```

**Why a GitHub App and not a token you paste in.** A personal access token in
our database is a long-lived credential to your source code; a breach hands over
everything. An App mints installation tokens that **expire in an hour, live in
memory, and are never written down**. A tool that assesses your security
shouldn't be storing your keys.

The token reaches git through `--config-env=http.extraHeader`, never the URL —
a URL-embedded token persists into the clone's `.git/config` and shows in
process listings. There's a test that clones with a marker credential and greps
the checkout to prove no trace remains.

### 7. Editing that keeps history honest

| Project state | Repository URL | Name |
|---|---|---|
| No scans yet | editable | editable |
| Only failed scans | **editable** | editable |
| A scan pending or running | frozen — worker holds the old target | editable |
| Any scan completed | **frozen** | editable |

**Why the split.** The reason people want to edit a URL is almost always a
typo — and a typo means the scan *failed*, which produced no score and no
findings, so nothing is falsified by fixing it. A **completed** scan is
different: repointing the project would leave its history describing a
repository the project no longer names.

Scan records are immutable except for a name. Timestamps and results describe
what happened, and a record that could be rewritten is worth nothing as
evidence.

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
# Infrastructure only — leave the app on your host for fast reloads
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
# Backend — 805 tests. Needs Postgres running.
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .

# Frontend — 68 tests
cd frontend
npm run typecheck && npm run lint && npm run test && npm run build
```

Tests run against a **real Postgres**, not SQLite. The schema uses JSONB, native
enums and `ON DELETE CASCADE`, none of which SQLite reproduces — a SQLite suite
would pass while the real database rejected the same code. A disposable
`sentinelops_test` database is created and dropped per run.

### Database migrations

```bash
uv run alembic upgrade head                          # apply
uv run alembic revision --autogenerate -m "message"  # create
uv run alembic downgrade -1                          # undo one
```

> **Two things Alembic won't do for you.** It never autogenerates
> `server_default`, so adding a `NOT NULL` column produces a migration that
> works on an empty database and fails on a populated one — declare it on the
> column and test against real rows. And it won't drop Postgres `ENUM` types in
> a downgrade, so those need explicit `sa.Enum(name=...).drop()` calls. Newer
> columns use plain `String` with a Python enum for exactly this reason.

### Adding a scanner

Three steps:

1. Create `backend/app/scanners/<category>/scanner.py` with a class exposing
   `category: str`, `CHECKS: tuple[CheckSpec, ...]`, and
   `scan(repo: RepositoryIndex) -> list[CheckResult]`
2. Register it in `backend/app/scanners/registry.py`
3. Add `backend/tests/test_<category>_scanner.py`

```python
class MyScanner:
    category = "reliability"
    CHECKS = (_HEALTH, _TIMEOUTS)

    def scan(self, repo: RepositoryIndex) -> list[CheckResult]:
        return [self._check_health(repo), self._check_timeouts(repo)]
```

Scanners are **synchronous** and receive a pre-built `RepositoryIndex` — the
tree is walked once per scan, not once per scanner. The worker dispatches them
off the event loop.

Conventions: impacts sum to exactly the category weight; one finding per
*problem* rather than per file; read `production_files`; guard service-only
checks with `repo.is_service`; return a result for **every** declared check —
a shared test enforces it.

### The API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/signup` · `/auth/login` | Rate-limited, constant-time |
| `GET POST` | `/projects` | List and create |
| `GET PATCH DELETE` | `/projects/{id}` | Read, edit, remove |
| `POST GET` | `/projects/{id}/scans` | Start a scan · scan history |
| `GET PATCH` | `/scans/{id}` | Poll a scan · name it |
| `GET` | `/scans/{id}/findings` | What's wrong |
| `GET` | `/scans/{id}/checks` | What was checked, and the outcome of each |
| `GET` | `/scans/{id}/comparison` | Versus the previous scan |
| `GET` | `/github/install` · `/setup` · `/installations` · `/repositories` | Private repos |

`GET /scans/{id}` is polled every 3 seconds while a scan runs, so it stays one
indexed row read. Findings, checks and comparisons each get their own endpoint,
fetched once when needed, rather than riding along with every poll.

---

## How it works

```
Frontend (React)  ──►  API (FastAPI)  ──►  Postgres
                            │                 ▲
                            ▼                 │
                       Redis queue ──► Worker ┘
                                          │
                                          ▼
                   clone → index → 6 scanners → 27 checks → score
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
- **`scanners/` imports nothing from the app.** A scanner is testable against a
  directory in `tmp_path` with no database, no queue, no HTTP.
- **`utils/storage.py` and `utils/queue.py`** are the only files allowed to know
  about a cloud SDK. There are currently **zero** cloud SDK dependencies, so the
  containers run anywhere; a third boundary (`SandboxRunner`) joins them in the
  next phase.

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
- Credentials are redacted from git output before it reaches a log or a database

Passwords use bcrypt, hashed off the event loop. The auth token is an `httpOnly`
cookie rather than localStorage, since a tool that assesses other people's
security shouldn't use an XSS-readable token store. Login runs in constant time
whether or not the account exists, so it can't be used to enumerate users. Auth
endpoints are rate limited.

---

## Roadmap

- [x] **Foundation** — auth, database, API, Docker
- [x] **Scanning engine** — 6 scanners, 27 checks, private repositories
- [x] **Explainability** — commit context, failure reasons, check outcomes,
      scan comparison, editing
- [ ] **Security tooling** — Gitleaks, Trivy, Semgrep and OSV, each in its own
      sandbox, behind a `SandboxRunner` boundary
- [ ] **Reporting** — PDF export
- [ ] **Production deployment** — CI/CD, k6 load testing, Cloud Run

Deliberately deferred: CI for SentinelOps itself (its own scanner correctly
flags this), Redis caching of scan status, PgBouncer, and metrics — the last
being the other finding it reports about itself.

## Stack

| Layer | Choices |
|---|---|
| **Frontend** | React, TypeScript, Vite, Tailwind, shadcn/ui, TanStack Query, Recharts, Vitest |
| **Backend** | FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), Alembic, PostgreSQL, Redis + arq, bcrypt, PyJWT, slowapi, pytest |
| **Infrastructure** | Docker Compose, multi-stage images with separate API and worker targets |

Notable choices: `bcrypt` directly rather than passlib (unmaintained, breaks
against bcrypt 4.x), PyJWT rather than python-jose (unmaintained,
signature-verification CVEs), and async SQLAlchemy end to end because
retrofitting sync→async later touches every signature.

## License

Apache 2.0 — see [LICENSE](LICENSE).
