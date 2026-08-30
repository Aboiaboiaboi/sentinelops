<div align="center">

# SentinelOps

**Is this application ready for production?**

Point SentinelOps at a Git repository. It clones it, checks it against 31
things that commonly go wrong before launch, and gives you a score out of 100
with specific findings — what's wrong, why it matters, and what to do about
it. It also shows which commit it looked at, what changed since the last
scan, and what it *confirmed was fine*, not just what broke.

[Quick start](#quick-start-5-minutes) ·
[What it checks](#what-it-checks) ·
[Beyond the score](#beyond-the-score) ·
[For developers](#for-developers) ·
[How it works](#how-it-works)

</div>

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

First run takes a few minutes to download and build. It starts five things:
a database, a queue, a one-off database setup job, the API, and the
background worker that actually runs the scans.

Two more short jobs run alongside them and exit on their own — they're
downloading the vulnerability database and rule set the security tools need
(~100 MB, ~1.1 GB on disk once unpacked). This is the only moment anything
gets downloaded; the scanners themselves run with no network access at all.
You don't need to wait for this — the app is usable right away, and until
that data's ready, the checks that depend on it just report themselves as
"not run yet" instead of pretending your repo is clean.

Check it's alive:

```bash
curl localhost:8000/health
```

You should see `{"status":"ok"}`. Prefer clicking to typing? Open
<http://localhost:8000/docs> for the interactive API docs instead.

### 3. Start the app

```bash
cd frontend
npm install
npm run dev
```

### 4. Use it

Open **<http://localhost:5173>**, create an account (it's local — nothing
leaves your machine), add a repository URL, and click **Run scan**.

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

**Port already in use** — something else is on 8000, 5173, 5432 or 6379.
Stop it, or change the port mapping in `docker-compose.yml`.

**The page loads but nothing works** — check the API is up with
`curl localhost:8000/health`. If not, `docker compose logs backend`.

**A scan stays "Queued" forever** — the worker isn't running.
`docker compose ps` should show `worker` as healthy; `docker compose logs worker`
will say why if it isn't.

**The security checks all say "errored"** — the worker couldn't start a
sandbox container. `docker compose logs worker | grep sandbox` says why:
usually either Docker itself isn't reachable, or that cache from step 2
hasn't finished yet. You can re-run just that step with
`docker compose up warm-trivy warm-semgrep`.

**Want to see the app without a backend at all?**

```bash
cd frontend
echo "VITE_USE_FIXTURES=true" > .env.local
npm run dev
```

Every screen works against built-in sample data, including the
live-updating scan progress. Set it back to `false` to use the real thing.

</details>

---

## What it does

You give it a repository URL. A few seconds later you get something like
this — a real scan of **this repository, run just now**, not a mockup:

```
sentinelops                                        98 / 100    Grade A
6 of 6 categories reported
31 checks: 26 passed · 4 skipped · 1 failed

  Security         25 / 25   ████████████████████
  Architecture     20 / 20   ████████████████████
  Reliability      20 / 20   ████████████████████
  Scalability      10 / 10   ████████████████████
  Observability    10 / 10   ████████████████████
  Deployment       13 / 15   █████████████████░░░
```

One finding, and it's deliberate rather than overlooked:

> **Container granted host-level access** · HIGH · −2
> `docker-compose.yml` mounts the Docker socket into a container, which is
> effectively root on the host machine. This is a real and common pattern in
> local development (it's how the security scanners here start their own
> sandboxed tool containers) — but it's the kind of line that's easy to
> forget about and accidentally carry into something that actually ships.
>
> **Recommendation:** keep this out of anything deployed for real. If a
> container genuinely needs it, grant only the specific permission it needs
> instead of full access.

It's left on the scoreboard on purpose — it's a real trade-off, not a bug, and
the finding exists to make sure it never quietly ends up somewhere it
shouldn't.

Every scan can also be downloaded as a PDF with the same score, breakdown,
findings, and all 31 checks (including the ones skipped, and why).

It only **reads** the code. It never runs the repository, deploys it, or
changes anything in it.

---

## What it's for

**A backend service you're about to put into production.** Something
containerized, that serves HTTP, talks to a database, and might eventually
run as more than one copy — a FastAPI/Django/Express/Rails/Spring app, headed
for something like Cloud Run, Fly, Render, ECS, or Kubernetes.

That's the shape every check assumes, and the question they're all really
asking is **"what did we forget?"** — not the interesting problems, the
boring fatal ones: no CI, no healthcheck, container running as root, an
unpinned base image, a credential committed months ago, no timeout on an
outbound API call, session data kept in memory (works fine until you run a
second copy of the app).

| Good fit | Why |
|---|---|
| A product or SaaS API before launch | Every check applies, and 100 is genuinely reachable |
| Internal tools and admin dashboards | Usually the worst offenders, because "it's only internal" |
| A codebase you've just inherited | 31 concrete answers beats a week of reading unfamiliar code |
| One repo, scanned repeatedly over time | Watching the score move matters more than any single number |

| Poor fit | Why |
|---|---|
| Static sites, libraries, mobile and CLI apps | Nothing gets deployed as a service, so most checks don't apply |
| Notebooks and research code | There's no service here to assess |
| A monorepo holding several services | It scores the repo as one unit, so one weak service can hide inside a good average |

Two honest limits worth knowing. **Scores only compare like with like** — a
CLI tool skips all three scalability checks, so it can never reach 90, and
its 85 doesn't mean the same thing as a web service's 85. And **it never runs
your code**, so anything that only shows up at runtime is invisible to it.
Think of it as a readiness checklist that shows its work, not a penetration
test.

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

All six categories are fully working — a genuinely clean repository can score
a real 100. And a category that couldn't be assessed **contributes nothing**
rather than being quietly left out of the math, so a partial scan can never
pass itself off as a thorough one.

Same idea applies to individual checks: if every check in a category gets
*skipped* (none of it applied), that category earns zero, not full marks.
Scalability on a small CLI tool is the clean example — all three of its
checks are about how the app behaves running as multiple copies, none of
which applies to a CLI tool, so scoring it a full 10 would be rewarding work
nobody did. One real passing check is enough to keep the category alive; the
zero only kicks in when literally nothing could be checked.

The security category mixes real tools with pattern-matching:

- **Gitleaks** looks for committed credentials. It runs with no network
  access, and anything it finds gets redacted before SentinelOps ever sees
  it — so a finding says *that* a secret is exposed, never what the secret
  actually is.
- **Trivy** checks whether your dependencies have a known, published
  vulnerability, against a database that's downloaded ahead of time (the
  scanner itself has no internet access when it runs). A project with no
  recognizable dependency file is *skipped*, not passed — nothing was
  actually checked.
- **Semgrep** looks for dangerous *patterns* in the code itself — things
  like user input flowing straight into a shell command. Only its
  highest-confidence rules are used; the full rule pack is full of "maybe
  worth a look" suggestions, and a scanner that nags about maybes stops
  getting trusted.

The remaining five security checks (credential files, debug mode, TLS
overrides, container secrets, `.gitignore`) are simple pattern checks rather
than full tools — cheap to run, tested against real repositories, and
measured at zero false positives so far.

### How it avoids false alarms

A scanner that flags things that are actually fine gets ignored — and an
ignored scanner is useless. So it's deliberately conservative:

- **Doesn't-apply is reported as skipped, never as failed.** A CLI tool
  isn't expected to have a web health-check endpoint, and that's shown
  honestly as "skipped," not silently marked as passing either.
- **Test code is judged differently than real code.** A swallowed error in
  a test file is normal. A fake API key in test fixtures isn't a leaked
  secret.
- **Generated code is left alone.** Telling you to refactor a 4,000-line
  auto-generated file isn't useful advice.
- **A filename alone never triggers a finding.** A `.env` file full of
  placeholder values is a template, not a leak; a `.pem` file is only
  flagged if it actually looks like a real private key.

Two honest trade-offs that come with being this careful:

- **A real secret hidden inside a test folder can slip through.** The
  alternative is flagging your own test fixtures constantly — on a clean
  checkout of this very repo, Gitleaks finds seven "leaks," and all seven
  are the security scanner's own fake test data.
- **An empty repository doesn't get an easy pass either.** No real code
  means nothing could actually be verified, so it scores 0 — not partial
  credit for problems that simply weren't there to find. (An earlier
  version of this scored an empty repo 77, which was the bug that got this
  fixed.)

---

## Beyond the score

A single number is hard to trust on its own. Every scan comes with the
context to actually check it:

| | What you get | Why it matters |
|---|---|---|
| **Commit context** | The exact commit that was scanned — SHA, message, author, date | "The score dropped 6" becomes "the score dropped 6 *at this specific commit*" |
| **Every check's outcome** | All 31 checks, each marked passed, failed, skipped (with a reason), or errored | A perfect score can show you what was actually verified, not just that nothing complained |
| **Comparison to the last scan** | Score and per-category movement, plus exactly which checks changed | Shows regressions first — what got worse matters most |
| **Failure diagnostics** | If a scan itself fails, you get which category, a plain explanation, and a suggested fix | Better than a bare "scan failed" with no next step |
| **PDF report** | `GET /scans/{id}/report` — the full score, breakdown, findings, and all 31 checks as a downloadable document | Something you can attach to a ticket or hand to someone without a login |

The comparison feature is deliberately cautious — it'll refuse to show a
before/after difference when: the scoring rules themselves changed between
scans (that would measure a change in SentinelOps, not your code), a category
stopped being checkable (not a real regression), or a check is brand new
since the last scan (nothing to compare it to yet).

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

> Use `python -m app.workers.main`, **not** the `arq` CLI directly — the CLI
> sets up its own logging after settings are already loaded, which leaks
> plain-text lines into what's supposed to be a clean JSON log stream.

> **A worker running directly on your machine has no sandbox** unless you
> turn it on, so the tool-backed security checks will honestly report
> themselves as *errored* rather than fake a pass. To actually run them, set
> `SANDBOX_ENABLED=true` (leave `SANDBOX_VOLUME` empty) and
> `SANDBOX_CACHE_VOLUME=sentinelops_sandbox_cache`, after
> `docker compose up warm-trivy warm-semgrep` has downloaded what they need.

**Frontend:**

```bash
cd frontend
npm install
npm run dev                              # :5173, proxies /api to :8000
```

The dev server forwards `/api` requests to the backend and strips that
prefix off — this keeps the browser thinking it's talking to one single
origin, which is why the auth cookie can be `httpOnly` (invisible to
JavaScript, so it can't be stolen via an XSS bug) without breaking anything.

### Running the checks

```bash
# Backend — 948 tests. Needs Postgres running. The sandbox integration tests
# skip themselves automatically if no Docker daemon is available.
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .

# Frontend — 98 tests
cd frontend
npm run typecheck && npm run lint && npm run test && npm run build
```

Tests run against a **real Postgres**, not SQLite. The database schema uses
features SQLite doesn't actually support (JSONB, native enums, cascading
deletes) — a SQLite-based test suite could pass while the real database
would reject the same code. A throwaway `sentinelops_test` database gets
created and dropped for each run.

### Database migrations

```bash
uv run alembic upgrade head                          # apply
uv run alembic revision --autogenerate -m "message"  # create
uv run alembic downgrade -1                          # undo one
```

> **Two things Alembic (the migration tool) won't figure out for you.** It
> won't auto-generate a default value for existing rows unless you write it
> yourself — so a new required column can pass on an empty test database and
> then fail the moment it hits a table with real rows in it. Always write
> the default explicitly and test against real data. It also can't undo
> creating a Postgres enum type on its own — that needs an explicit line to
> drop it, which is part of why newer columns use plain strings instead.

### Watching a scan

```bash
docker compose logs -f worker
```

Every log line is structured JSON, so you can filter it like data instead
of scrolling text:

```bash
docker compose logs worker | grep '"category scanned"'
```

### Adding a tool-backed check (Gitleaks/Trivy/Semgrep style)

Each external tool lives in its own file under
`backend/app/scanners/security/tools/`, and each one is responsible for
three things: how to run the tool in its sandbox, how to parse what it
prints, and how to turn that into a pass/fail/skip result. Easiest way in:
copy `trivy.py` and adapt it.

Before writing any code, **run the actual tool by hand first and check its
exit codes carefully** — this bit two real tools already. Gitleaks exits
with the same code for "found leaks" as it does for "couldn't even read the
folder," so it has to be run with a flag that separates the two. Trivy
exits 0 when it finds vulnerabilities but 1 if its database is missing.
Get this backwards and the scanner will happily report a broken tool run as
"repository is clean" — which is worse than not checking at all.

That's why the rule here is strict: a tool that failed to run is always
`errored`, never `passed`. A question that genuinely doesn't apply (no
lockfile, say) is `skipped`, with a reason attached. The tool's raw error
output goes to the logs, never into the stored results.

### Adding a whole new scanner (a new category)

Three steps:

1. Create `backend/app/scanners/<category>/scanner.py` — a class with a
   `category` name, a list of `CHECKS`, and a `scan()` method that returns
   results
2. Register it in `backend/app/scanners/registry.py`
3. Add a test file: `backend/tests/test_<category>_scanner.py`

Every check returns its own explicit outcome, so "we checked this and it
was fine" stays distinct from "this didn't apply here":

```python
def _check_health(self, repo: RepositoryIndex, has_health: bool) -> CheckResult:
    if not repo.is_service:
        return skipped(_HEALTH, "only asked of something that serves traffic")
    if has_health:
        return passed(_HEALTH)
    return failed(_HEALTH, ScanFinding(...))
```

Scanners themselves are plain, ordinary (synchronous) functions — the repo
gets read into memory once, then every scanner works off that same copy
instead of each one re-reading the filesystem.

A few conventions worth knowing: each category's point values must add up
exactly to its weight; report one finding per underlying problem, not one
per affected file; and every declared check must return *some* result for
every scan — there's a shared test that checks this across several sample
repos, so a check you forget to actually run fails the test suite instead
of silently pretending to pass.

### Why the security tools run at the same time, and why there's a cap

The three tool-backed security checks (Gitleaks, Trivy, Semgrep) run
concurrently rather than one after another, so the whole category takes as
long as the *slowest* one, not the sum of all three.

That concurrency has a deliberate limit
(`SANDBOX_MAX_CONCURRENT`, default 6). Each sandboxed container has its own
memory cap, but nothing stops many containers existing at once — several
scans running at the same time, each spinning up three tool containers,
adds up fast enough to exhaust a normal machine's memory. The limit was set
by actually measuring real memory use (Semgrep ~270MB, Gitleaks ~60MB,
Trivy ~40MB at peak) rather than guessing, and extra scans simply queue
briefly instead of being allowed to pile up unbounded.

Measured effect on this repository: the security category went from
**33.5s to 22.9s** once all three tools ran together instead of one after
another.

### The CI/CD pipeline

Two separate GitHub Actions workflows, doing two different jobs:

**`ci.yml` — runs on every push and pull request.** Four stages, each
gated on the one before it passing:

| Stage | What it does |
|---|---|
| Backend tests | Runs the full pytest suite (948 tests) against a real Postgres, plus linting and formatting checks |
| Frontend tests | Type-checking, linting, unit tests, and a production build |
| Image build | Actually builds the API and worker Docker images, then starts each one to check it doesn't crash on launch (a missing dependency can build fine and still fail the moment it runs) |
| Publish to GHCR | Only runs when a version tag (`v1.2.3`) is pushed, and only after all three stages above have passed — pushes the built images to GitHub's container registry, tagged with the version and commit |

That last stage existing only for tagged releases — not every push — means
the registry only ever holds an image that's actually passed everything.

**`deploy.yml` — runs on every push to `main`, and deploys for real.** This
one logs into the live AWS server and does the actual release: pulls the
new code, rebuilds the containers there directly from source (it doesn't
use the GHCR images above at all — see the note in the deploy folder's own
README for why), and swaps each service over one at a time, only moving to
the next once the previous one reports healthy. If a final health check
fails at the end, it automatically rolls back to the last known-good
version rather than leaving the site broken.

The monitoring stack (Prometheus/Grafana/Loki) is deliberately **not**
part of either pipeline — it's set up once by hand and left running,
the same way the database is, rather than being restarted on every deploy.

---

## How it works

**Inside the app**, when you click "Run scan":

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

The API doesn't do the scanning itself — it just saves a record and drops a
job in a queue, so it can reply instantly no matter how big the repo is. A
separate worker process picks that job up, does the actual clone and scan,
and the browser just polls for a result. Each of the 6 categories runs
independently, so if one of them breaks, the scan still finishes with the
rest — that one category just loses its points instead of the whole scan
failing.

**Around the app**, in production:

```
Terraform (deploy/aws/)
    │  provisions
    ▼
AWS EC2 instance ──► Caddy (HTTPS + reverse proxy)
    │                      │
    │                      ├──► the app itself (diagram above)
    │                      └──► Grafana (dashboards, password-protected)
    │
    └──► Prometheus + Loki + Alloy
             (collect metrics & logs from every container)
```

The server itself — the VM, its network rules, its storage — is created by
Terraform, so standing up the whole environment from nothing is one command,
not a checklist. Caddy sits in front of everything as the single public
door: it gets the HTTPS certificate and decides what goes where. Alongside
the app, a monitoring stack watches it — Prometheus polls the app for
numbers (traffic, queue size), Loki collects logs, and Grafana turns both
into dashboards.

### Project layout

| Path | Contents |
|---|---|
| `backend/app/api/` | The HTTP layer — thin, just calls into `services/` |
| `backend/app/services/` | The actual business logic, shared by both the API and the worker |
| `backend/app/scanners/` | Takes a repository in, returns check results out — nothing else |
| `backend/app/scanners/security/tools/` | One file per external tool (Gitleaks/Trivy/Semgrep) |
| `backend/app/workers/` | Queue handling and repository cloning |
| `backend/app/utils/sandbox.py` | The only code allowed to start a container that runs on a stranger's code |
| `backend/app/models/` `schemas/` | Database tables, and API request/response shapes — kept deliberately separate |
| `frontend/src/api/` `hooks/` | Functions that call the backend, and the React hooks wrapping them |
| `frontend/src/pages/` `components/` | Screens, and the reusable pieces they're built from |
| `deploy/aws/` | Terraform — creates the actual live server this runs on |
| `deploy/compose/` | The portable version — same app, one Docker Compose file, runs on any Linux server |

A couple of boundaries worth knowing about:

- **API response shapes (`schemas/`) are kept separate from database tables
  (`models/`)** on purpose. The `User` table has a password hash column; no
  API response type even references it, so there's no way for it to
  accidentally leak in a response.
- **The scanning code doesn't know about the database, the API, or the
  cloud.** It only knows how to read a repository and return results — which
  means every scanner can be tested against a plain folder on disk, no real
  app running required.

### Security

Since this app clones and inspects other people's code, it treats every
repository as untrusted input:

- Clones are shallow (no full history), and only the code itself — no
  submodules, no large file storage
- Hard limits on how long a clone can take and how much it can download
- Git hooks are disabled, so a repo can't run its own scripts during clone
- Symbolic links are never followed, so a link pointing outside the repo
  (like `/etc`) can't be used to read the host machine
- Every clone is deleted after the scan, success or failure
- The worker runs as a non-root user, and only the worker (not the API) has
  `git` installed at all

The three tool-backed security checks go further, since they're the only
part of this system that actually **runs** other people's code (via
Gitleaks/Trivy/Semgrep): each runs in its own locked-down container with no
network access at all, read-only filesystem access, every extra permission
stripped, and a memory/CPU cap. If a scan needed to trust a sandbox, it can't
— the sandbox refuses to run rather than run unsafely.

On the account side: passwords are hashed with bcrypt, the login session is
an `httpOnly` cookie (invisible to JavaScript, so it can't be stolen via a
script-injection bug), login takes the same amount of time whether or not
the account exists (so it can't be used to guess valid emails), and login
attempts are rate-limited.

### How tied to one cloud is this, really

Not "runs anywhere with zero changes" — that claim is usually either untrue
or very expensive to actually achieve. What's true here is narrower and
easier to check: only a **small, named part** of the code knows it's talking
to a specific cloud.

| Layer | What it is | Cost to move it |
|---|---|---|
| **Fully swappable** | Three files — `utils/queue.py`, `utils/storage.py`, `utils/sandbox.py` — are the *only* places allowed to know about a specific cloud service or SDK | Write one new file. Nothing in the API, business logic, or scanners changes at all |
| **Already works elsewhere** | Object storage (`S3Storage`) speaks the standard S3 API, which AWS, Cloudflare R2, DigitalOcean Spaces, and MinIO all understand. The sandbox just needs a Docker daemon, which every cloud's VM has | Change a URL and a bucket name — no code |
| **Just a connection string** | Postgres and Redis | Point at a managed version of either (RDS, Neon, Upstash, etc.) — same protocol, different address |
| **Actually cloud-specific** | The Terraform itself — `deploy/aws/` provisions real AWS resources (EC2, S3, IAM) by name. This is the live, currently-running deployment | Terraform for a different cloud would need to be written from scratch — which is exactly why `deploy/compose/` also exists: same app, same Docker images, no cloud-specific code, runs on any plain Linux server |

Two rules keep the "swappable" claim actually true instead of aspirational:
no cloud SDK is installed by default (they're optional, only pulled in if
that feature's actually used), and the application code itself never
hardcodes a project name, region, or bucket — those are always environment
variables, set by whatever infrastructure is running it.

---

## Roadmap

- [x] **Foundation** — accounts, database, the API, and the whole app running
      in Docker
- [x] **Scanning engine** — all 6 scoring categories working, plus support
      for private (not just public) repositories
- [x] **Explainability** — every scan shows which commit it looked at, real
      reasons when a scan fails instead of a bare error, what actually
      passed rather than just what broke, and scan-to-scan comparison
- [x] **Real security tooling** — swapped simple pattern-matching for actual
      tools (Gitleaks, Trivy, Semgrep), each running sandboxed and
      concurrently so they don't add up in total time
- [x] **PDF reports** — every scan can be exported as a document, generated
      on demand and cached so re-downloading the same scan is instant
- [x] **Portable deployment** — a Docker Compose setup (`deploy/compose/`)
      that runs the whole app on any plain Linux server on any cloud, not
      tied to one provider
- [x] **Live production deployment** — actually running on a real AWS
      server (`deploy/aws/`), reachable over the internet with a real
      domain and HTTPS
- [x] **Full observability** — Prometheus, Grafana, and Loki running
      alongside the live app: real-time dashboards for traffic, the scan
      queue, container health, and searchable live logs
- [ ] **Load testing** — not yet done; how the app behaves under heavy
      concurrent scan traffic is still unverified

Private repositories are accessed through a GitHub App rather than stored
personal access tokens, so credentials expire automatically and nothing
long-lived is ever saved.

SentinelOps has been used to scan itself along the way, and it's caught real
things worth admitting to: it once flagged its own missing CI setup (now
fixed — see [.github/workflows/ci.yml](.github/workflows/ci.yml)), an
outdated dependency (upgraded), and an oversized file (split up). The one
finding still standing on purpose is the Docker socket mount described near
the top of this page — that one's a deliberate trade-off, not an oversight.

## Stack

Everything actually used, and a plain reason for each:

**Frontend**

| Tool | Why this one |
|---|---|
| React + TypeScript | Standard, typed, huge ecosystem — no exotic choice here |
| Vite | Fast dev server and build, minimal config compared to older bundlers |
| Tailwind + shadcn/ui | Utility CSS plus accessible, unstyled component primitives — fast to build with, doesn't fight you |
| TanStack Query | Handles server data fetching/caching/polling (used for live scan progress) without hand-rolling it |
| Recharts | The category score bars and comparison charts |
| Vitest | Fast, works natively with Vite's config, no separate test bundler needed |

**Backend**

| Tool | Why this one |
|---|---|
| FastAPI | Async-native, automatic request validation and OpenAPI docs from the same type hints |
| Pydantic | Backs FastAPI's validation, also used for settings/config loading |
| SQLAlchemy 2.0 (async) | The database layer, using its modern async API to match FastAPI |
| Alembic | Generates and runs database schema migrations |
| PostgreSQL | The real database — chosen over SQLite specifically because the schema leans on features (JSONB, native enums, cascading deletes) SQLite can't faithfully reproduce |
| Redis + arq | The job queue — scans are queued here and picked up by the background worker, so the API never blocks waiting for a scan to finish |
| bcrypt | Password hashing — used directly instead of the popular `passlib` wrapper, which is unmaintained and breaks on newer bcrypt versions |
| PyJWT | Session tokens — chosen over `python-jose`, which is unmaintained and has had signature-verification vulnerabilities |
| slowapi | Rate limiting on auth endpoints, to slow down brute-force login attempts |
| pytest | The whole backend test suite, ~948 tests |

**Infrastructure & ops**

| Tool | Why this one |
|---|---|
| Docker Compose | Runs the whole stack (database, queue, API, worker, frontend) consistently in dev and in production |
| Caddy | The production reverse proxy — handles HTTPS certificates automatically, no manual cert setup |
| Prometheus | Collects numeric metrics (request rates, latency, queue depth) from the running app |
| Grafana | Turns those metrics (and logs) into dashboards |
| Loki + Grafana Alloy | Log collection and search — Alloy ships every container's logs to Loki as they're printed |
| GitHub Actions | CI (tests/build on every push) and CD (actual deployment to the live server) — see below |

## License

Apache 2.0 — see [LICENSE](LICENSE).
