<div align="center">

# SentinelOps

**Is this application ready for production?**

Point SentinelOps at a Git repository. It clones it, runs 31 checks across six
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
sentinelops                                        88 / 100    Grade B
6 of 6 categories reported
31 checks: 23 passed · 4 skipped · 4 failed

  Security         22 / 25   ██████████████████░░
  Architecture     17 / 20   █████████████████░░░
  Reliability      20 / 20   ████████████████████
  Deployment       13 / 15   █████████████████░░░
  Scalability      10 / 10   ████████████████████
  Observability     6 / 10   ████████████░░░░░░░░
```

Deployment lost two points on this run for a real reason, not a stale one:
`docker-compose.yml` mounts the Docker socket into the worker so `DockerSandbox`
can start sibling containers for the tool-backed security checks. Root-on-the-host,
granted deliberately, in a file marked development-only — the finding exists to
catch exactly the case where that line gets copied into something that ships.
Architecture lost three for a file that grew past a size anyone should be
expected to review in one sitting. Neither is hidden or excused; both are on the
scanner's own report about itself.

The three points Security lost are a published advisory against a dependency
this project pins, found by Trivy on the run above. It is reported rather than
suppressed, because whether the affected code path is reachable is a judgement
the tool should not quietly make on your behalf — and suppressing it was the
tempting option this project exists to argue against.

Each finding tells you what it found and what to do:

> **No metrics or error tracking** · MEDIUM · −4
> No metrics, tracing, or error-reporting library was found. Nothing measures
> latency, error rate or throughput, and no exception is reported anywhere — so
> the first notice that the service is broken comes from whoever is using it.
>
> **Recommendation:** Export a handful of metrics that describe user-visible
> health, and send unhandled exceptions to an error tracker so they are seen
> without being hunted.

All four findings are fair — a dependency upgrade this project owes, a
development-only socket mount that must never reach a real deployment, one file
that outgrew a single sitting's worth of review, and the missing metrics above.
None is disputed; two are already tracked on the roadmap below.

Every scan is downloadable as a PDF that says the same things this page does —
the score, the category breakdown, each finding with its recommendation, and all
31 checks including the ones that were skipped and why.

It **reads** code and configuration. It never runs the repository, deploys
anything, or changes it.

---

## What it's for

**A backend service you are about to deploy.** Something containerised, that
serves HTTP, talks to a database, and might one day run as more than one copy.
Django, FastAPI, Express, NestJS, Rails, Spring Boot — on Cloud Run, Fly,
Render, ECS or Kubernetes.

That is the shape all 31 checks assume, and the question they answer is
**"what did we forget?"**. Not the interesting problems — the boring, fatal
ones. No CI. No healthcheck. Container running as root. Base image unpinned. A
credential committed last March. No timeout on the payment API call. Sessions
kept in process memory, which works perfectly until the day you scale to two
instances.

| Good fit | Why |
|---|---|
| A product or SaaS API before launch | Every check applies, and 100 is genuinely reachable |
| Internal tools and admin dashboards | Usually the worst offenders, because "it's only internal" |
| A codebase you have just inherited | 31 answers about what is actually there beats a week of reading |
| One repository per service, scanned repeatedly | The score moving is worth more than the score |

| Poor fit | Why |
|---|---|
| Static sites, libraries, mobile and CLI apps | Nothing is deployed, so most of the rubric does not apply |
| Notebooks and research code | No service to assess |
| A monorepo holding several services | It scans a repository as one unit, so one weak service hides inside a good average |

Two honest limits. **Scores only compare like with like** — a CLI tool has all
three scalability checks skipped, so it cannot pass 90, and its 85 is not a web
service's 85. And **it never runs your code**, so anything that only appears at
runtime is invisible to it. This is a readiness checklist that shows its work,
not a penetration test.

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

**The security tool checks all say "errored"** — the worker could not start a
sandbox. `docker compose logs worker | grep sandbox` says which: an unreachable
Docker daemon, or a cache volume that has not been warmed yet. The warm jobs run
on `docker compose up` and can be re-run on their own with
`docker compose up warm-trivy warm-semgrep`.

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

Six categories, weighted to sum to 100, and 31 individual checks:

| Category | Weight | Checks | What it looks at |
|---|---:|---:|---|
| **Security** | 25 | 8 | committed credentials, leaked secrets (**Gitleaks**), vulnerable dependencies (**Trivy**), dangerous code patterns (**Semgrep**), debug mode, TLS overrides, container secrets, `.gitignore` |
| **Reliability** | 20 | 4 | health endpoint, request timeouts, swallowed errors, retries |
| **Architecture** | 20 | 5 | tests, dependency locking, file size, layout, documentation |
| **Deployment** | 15 | 8 | deployment config, image pinning, non-root user, healthcheck, build context, CI, signal handling, host isolation |
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
records *that* a credential is exposed and never the credential itself.

**Trivy** answers "does this project pin a version with a published
vulnerability?" — against a database warmed into a read-only volume, because the
sandbox has no network to fetch one. A repository with no lockfile it recognises
is *skipped*, not passed: nothing was demonstrated.

**Semgrep** answers "is the shape of this code dangerous whatever values flow
through it?" — a shell invoked with user input, a query built by string
formatting. Only rules it rates as **errors** are reported: the full
`p/security-audit` set includes a great deal of "consider whether" advice, and
noise is how a scanner earns a reputation for crying wolf.

The five regex checks that remain — credential files, debug mode, TLS overrides,
container secrets, `.gitignore` — were kept rather than routed through a tool.
They are dogfooded against real repositories, they cost nothing, and their
measured false-positive rate is zero.

### It tries hard not to cry wolf

A scanner that flags healthy code gets muted, and a muted scanner catches
nothing. So it only reports what it can evidence:

- **Checks that don't apply are skipped, not failed.** A CLI tool shouldn't have
  a health endpoint. A skip is always reported as a skip, never as a pass.
- **Test code is judged differently.** A swallowed exception in a test is fine,
  and a fake key in a fixture is a fixture.
- **Generated code is excluded.** "Split this 4000-line generated client into
  modules" is not advice anyone can act on.
- **A filename never convicts on its own.** A `.env` full of `changethis` is a
  template, a `.pem` is flagged only if its header says *private*, and a library
  named in a comment is not a library you use.

Two things this costs, both deliberate:

- **A real credential inside a test directory goes unreported.** The alternative
  is flagging your own fixtures — on a fresh clone of this repository, Gitleaks
  finds seven "leaks" and all seven are the security scanner's own test data.
- **It won't flatter an empty repository either.** No source code means nothing
  was assessed, so it scores 0 rather than collecting marks for problems nobody
  could find. It used to score 77.

---

## Beyond the score

A number on its own is hard to trust. Every scan also carries the context that
makes it checkable:

| | What you get | Why it exists |
|---|---|---|
| **Commit context** | The sha, message, author and date of the commit that was scanned | Turns "the score dropped 6" into "the score dropped 6 *at this commit*" |
| **What was checked** | All 31 checks with an outcome each — passed, failed, skipped with a reason, or errored when a tool could not run | A category at full marks can say *what it verified*, instead of merely having nothing to complain about |
| **Comparison** | Score and per-category movement against the previous scan, plus the exact checks that flipped | Regressions first, because what broke is what you need to see |
| **Failure diagnostics** | A category, a plain-language detail, and a suggested fix when a scan fails | A scan that just says "failed" is a dead end |
| **PDF report** | `GET /scans/{id}/report` — the same score, breakdown, findings and 31 check outcomes as a document | A scan you can attach to a ticket or hand to somebody who does not have a login |

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

> **A host-run worker has no sandbox** unless you ask for one, so the tool-backed
> security checks report *errored* — honestly, rather than passing. To run them,
> set `SANDBOX_ENABLED=true` (leave `SANDBOX_VOLUME` empty, which bind-mounts the
> clone by its real path) and `SANDBOX_CACHE_VOLUME=sentinelops_sandbox_cache`
> after `docker compose up warm-trivy warm-semgrep` has populated it.

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
# Backend — 948 tests. Needs Postgres running. The sandbox integration tests
# skip themselves when no Docker daemon is reachable.
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .

# Frontend — 98 tests
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

### Adding a tool-backed check

`backend/app/scanners/security/tools/` has one module per tool, and each owns
three things: the `SandboxSpec` that runs it, the parsing of its output, and the
translation into a `CheckResult`. Copy `trivy.py`.

Before writing a line of it, **run the tool by hand and record its exit codes**.
Both tools here had a trap: Gitleaks exits 1 for "leaks found" *and* for "I could
not read that directory" (so it runs with `--exit-code 0`), and Trivy exits 0 on
findings but 1 with no vulnerability database. Read either backwards and the
scanner reports a broken run as a clean repository — which is worse than
reporting nothing at all.

The rules that follow from that: a tool that could not run is `errored`, never
`passed`; a question that does not apply — no lockfile, say — is `skipped` with
a reason; the tool's own stderr goes to the log and never into the database; and
the spec sets `needs_cache` rather than naming a volume, so a scanner never
reads deployment configuration.

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

The three tool-backed checks run **concurrently**, so the security category
costs the slowest container rather than the sum of three. They are dispatched
through a `ThreadPoolExecutor` inside the scanner, which keeps the `Scanner`
protocol synchronous — a subprocess wait releases the GIL, so threads are the
right tool and the worker still dispatches a scanner with one `to_thread`.

Concurrency has a ceiling, and it is deliberate. `--memory` bounds what one
container may use; nothing bounds how many exist, and the real count is arq's
`max_jobs` multiplied by the tools a scanner runs at once — five scans of three
tools is fifteen containers, which at 512 MB apiece is 7.5 GB and more than a
default Docker Desktop VM has. `SANDBOX_MAX_CONCURRENT` bounds the containers
directly, so adding a fourth tool changes queueing rather than the memory
ceiling.

Its value came from measuring rather than from halving the alarming number.
Peak usage is Semgrep 271 MiB, Gitleaks 58 MiB, Trivy 39 MiB — a fraction of
what the limits allow, and a limit is not a reservation. Six is two scans' worth
of tools, and the queueing it causes is cheap: on a 2,531-file repository the
three tools cost about 34 container-seconds together, so waits are tens of
seconds against a 300s ceiling. Waiting is still *bounded*, because waiting
indefinitely would push a scan past `job_timeout`, get it cancelled mid-write
and retried — adding load exactly when there is already too much.

Measured on that repository, the security category went from **33.5s to 22.9s**,
which is the sum of three tools becoming the slowest of them.

---

## How it works

```
Frontend (React)  ──►  API (FastAPI)  ──►  Postgres
                            │                 ▲
                            ▼                 │
                       Redis queue ──► Worker ┘
                                          │
                                          ▼
                  clone → index → 6 scanners → 31 checks → score
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
| `backend/app/scanners/` | A repository in, check results out. No database, no API |
| `backend/app/scanners/security/tools/` | One module per sandboxed tool: build the spec, parse the output, return a `CheckResult` |
| `backend/app/workers/` | Queue tasks and repository cloning |
| `backend/app/utils/sandbox.py` | The container boundary. The only place that starts a process the repository can influence |
| `backend/app/services/report_*.py` | What the report says, how it is drawn, and when a stored copy may be reused — three files because the middle one is the replaceable part |
| `backend/app/assets/fonts/` | DejaVu, vendored. fpdf2's built-in fonts are latin-1 only, and repository text is not |
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
  container runtime. **Zero cloud SDKs are a default dependency** — the two
  that exist (`google-cloud-storage`, `boto3`) live in optional groups and are
  imported inside the class that needs them — so a default install runs and
  tests with none of them present; swapping Redis for SQS, or Docker for
  Cloud Run Jobs, is a change in one file. Two of the three default to
  refusing, for different reasons: with no container runtime a tool check
  reports *errored*, never *passed*; with no storage configured a write raises
  rather than being discarded, because a caller that believes it saved
  something and did not is worse off than one that got an error.

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

The security tools go further, because they are the only thing here that
*executes* third-party binaries against a stranger's code. Each runs in its own
container with **no network at all**, a read-only root filesystem, every Linux
capability dropped, `no-new-privileges`, bounded memory, CPU and process count,
as uid 65534, with the checkout mounted read-only. How many may run at once is
bounded too, so a busy worker queues rather than handing the host's OOM killer
a choice between Postgres and a scan. Its vulnerability database
arrives through a cache volume it can only read, because it has no way to fetch
one. Images are pinned by tag — an unpinned tool could change under a scan and
make a score change unexplainable — and the sandbox refuses to run at all rather
than run something unisolated.

Passwords use bcrypt, hashed off the event loop. The auth token is an `httpOnly`
cookie rather than localStorage, since a tool that assesses other people's
security shouldn't use an XSS-readable token store. Login runs in constant time
whether or not the account exists, so it can't be used to enumerate users. Auth
endpoints are rate limited.

### How much of this is tied to one cloud

Not "cloud agnostic" — that claim is usually either false or expensive. What
this is instead is **confined**, and the surface is small enough to name
exactly.

| Tier | What it is | What moving costs |
|---|---|---|
| **Confined** | Three modules — `utils/queue.py`, `utils/storage.py`, `utils/sandbox.py` — are the only places allowed to know about an execution backend or a cloud SDK. Each is a Protocol with a real implementation, a refusing default, and a `set_x()` called once at startup | A new class in one file. Nothing in `api/`, `services/` or `scanners/` changes, because none of them can name a bucket or start a container |
| **Already portable** | `S3Storage` speaks the S3 API, which most clouds speak too — real AWS, Cloudflare R2, DigitalOcean Spaces, MinIO, or GCS through its own interop endpoint. `DockerSandbox` runs on any Linux host with a Docker daemon, which is every cloud's VM | `STORAGE_ENDPOINT_URL` and a bucket name. Nothing to write |
| **Substitutable** | Postgres and Redis | A connection string. Cloud SQL, RDS, Neon, Upstash or a container are the same two protocols behind different hostnames |
| **Actually locked in** | `deploy/*.tf` — Cloud Run, Cloud SQL, Memorystore, GCP IAM | Infrastructure-as-code is provider-specific by nature, and this is the one deployment path that names a cloud. `deploy/compose/` is the answer for a target that must not: same application, one Docker Compose file, any VM |

Two rules keep the first two rows honest rather than aspirational. **No cloud
SDK is a default dependency** — `google-cloud-storage` and `boto3` live in
optional dependency groups and are imported inside the class that needs them,
so an install with neither present still runs, still tests, and fails loudly at
the boundary rather than silently at import. And **the application never names
a project, a region or a bucket**: those arrive as environment variables, set
by whatever infrastructure created them.

The honest summary: moving object storage between clouds is an environment
variable, moving the sandboxed scanners is nothing at all if the target is a
VM, and moving the managed-infrastructure deployment is a rewrite of
`deploy/*.tf` — which is what `deploy/compose/` exists to make optional rather
than mandatory.

---

## Roadmap

- [x] **Foundation** — auth, database, API, Docker
- [x] **Scanning engine** — all 6 scanners, plus private repositories
- [x] **Explainability** — commit context on each scan, real reasons when one
      fails, which checks passed rather than only what broke, scan-to-scan
      comparison, and editing that preserves history
- [x] **Security tooling** — real tools instead of regexes, each in its own
      sandbox behind a `SandboxRunner` boundary. Gitleaks, Trivy and Semgrep,
      run concurrently under a bounded container limit, so the category costs
      the slowest tool rather than the sum of them. OSV was dropped as a
      duplicate of Trivy
- [x] **Reporting** — PDF export, rendered on demand behind a `ReportRenderer`
      boundary and cached in object storage under a key that fingerprints what
      the document says, so renaming a scan produces a fresh document rather
      than a stale one
- [x] **Portable deployment** — `deploy/compose/`, a Docker Compose stack that
      runs on any Linux VM on any cloud's free trial: Caddy for TLS and the
      single-origin proxy, `S3Storage` for object storage against real S3, R2,
      Spaces or MinIO, and the same `DockerSandbox` development already uses.
      Verified end to end, sandbox included, self-scan included
- [ ] **Managed cloud deployment** — the Terraform under `deploy/*.tf` was
      applied once, to GCP; its billing account has since lapsed and the
      project sits frozen (`11-phase5-handoff.md`). Load testing and
      observability were never reached

Private repositories use a GitHub App rather than stored access tokens, so
credentials expire hourly and are never persisted.

Two of the findings SentinelOps reported about itself early on were its own
missing CI and its own missing metrics. **CI is now in
[.github/workflows/ci.yml](.github/workflows/ci.yml)** — added because the tool
kept saying so, which is the only honest way to ship something that grades other
people's repositories. Metrics remain deferred and are still the largest single
deduction above. The scan shown at the top of this page reports four findings
in total, not two — an unpatched advisory, the development-only Docker socket
mount, one file that outgrew a single sitting's review, and the missing
metrics — and all four are real, current, and taken from the same command
anyone can run.

The pipeline runs three independent jobs: backend lint, format and the full
suite against a real PostgreSQL service; frontend lint, types, tests and build;
and both container images built and then *started*, because a missing runtime
dependency is invisible to `docker build` and fatal on first run — which is
exactly how this project once shipped two images that crashed on import.

On a `v*` tag, and only once all three have passed, a fourth job publishes the
API and worker images to GHCR. They carry the version and the full commit SHA
and deliberately no `latest`: a moving tag makes "which image is running" a
question nobody can answer afterwards, and it is how a rollback quietly
redeploys the thing it was rolling back from.

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
