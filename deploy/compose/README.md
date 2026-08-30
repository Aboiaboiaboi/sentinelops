# The portable deployment

One Linux VM, running Docker, on whichever cloud's trial you have open —
AWS, Azure, Hetzner, or a spare machine on your desk. Where `../` (the
Terraform) is the one-cloud, managed-services, done-properly version, this is
the works-anywhere version. `11-phase5-handoff.md` names the trade explicitly:
no scale-to-zero, no managed database, no autoscaling, in exchange for a
deployment that names no cloud anywhere in it and moves between them in the
time it takes to `git clone` and run one script.

## What it needs from you first

**A domain, pointed at the VM.** Caddy (the container that terminates TLS and
serves the frontend) requests a Let's Encrypt certificate on first start and
does not fall back to plain HTTP — the application's `cookie_secure` setting
requires HTTPS in production (`backend/app/config.py`), so there genuinely is
no working IP-only mode. Point an A record at the VM's public IP before you
run anything.

**An object storage bucket.** `S3Storage` (`backend/app/utils/storage.py`)
works against real AWS S3 or anything that speaks the same API — Cloudflare R2,
DigitalOcean Spaces, a MinIO instance of your own — and `GcsStorage` still
works if you'd rather use Google's. Either way, create the bucket yourself
first; this deployment does not create one for you the way the Terraform does.

## Running it

On the VM, from a checkout of this repository:

```bash
cd deploy/compose
./provision.sh
```

The script installs Docker if it is missing, generates `.env` from
`.env.example` with a fresh `SECRET_KEY` and database password, then stops and
tells you which two values it would not guess (`DOMAIN`, `STORAGE_BUCKET`).
Fill those into `.env`, re-run, and it builds every image, brings up the full
stack, and populates the Trivy/Semgrep caches the sandboxed scanners need.

Every other value in `.env.example` has a working default or is genuinely
optional (the GitHub App credentials — public repositories work without them).

## What is actually running

The same six-service shape as the top-level `docker-compose.yml`, with the
differences that make it safe to expose:

| | Dev (`docker-compose.yml`) | Prod (`docker-compose.prod.yml`) |
|---|---|---|
| Postgres, Redis | Published to the host | Reachable only inside the compose network |
| Frontend | Vite dev server, port 5173 | Built once, served by Caddy on 80/443 |
| Backend | `ENVIRONMENT=development`, no domain | `ENVIRONMENT=production`, real `SECRET_KEY`, HTTPS-only cookie |
| Storage | N/A (LocalStorage, not deployment-safe) | `S3Storage` or `GcsStorage`, required |
| Origin | Vite proxies `/api` | Caddy proxies `/api` — same single-origin design, same reason |

The sandbox works exactly as it does in development: `SANDBOX_ENABLED=true`,
the worker mounts `/var/run/docker.sock` to start sibling tool containers. That
mount is root-on-the-host granted to the worker process — the same trade
`docker-compose.yml` documents at length — and it is the entire reason this
path exists rather than reimplementing the sandbox against a cloud-specific job
runner (`CloudRunJobSandbox` was Phase 5 milestone 3; it is not built, and this
deployment target is why not — see `11-phase5-handoff.md`). It is an accepted
risk here because nothing on this VM exposes the worker or the socket outside
the VM itself: no published port, and everything public passes through Caddy
first.

**One extra service: `frontend-build`.** It compiles the SPA and exits — it
is not part of the six long-running services, it is a one-shot, the same
shape as `migrate`. `frontend` (Caddy) reads whatever it most recently left in
the shared `frontend_dist` volume rather than having the build baked into its
own image. This is why: Caddy is the container holding the TLS certificate and
the public 80/443 listener, and if an ordinary app deploy rebuilt *that*
container the site would go down, however briefly, on every push. Splitting
the build out means an app deploy only ever runs `frontend-build`; Caddy
itself is untouched unless `Caddyfile` changes. Measured, not assumed — see
`.github/workflows/deploy.yml`'s header comment for the numbers.

## Continuous deployment

`.github/workflows/deploy.yml` deploys this to the instance on every push to
`main`: build, run migrations (blocking — a failed migration stops the deploy
before anything user-facing changes), then backend, worker, and
`frontend-build` in turn, each gated on its own healthcheck, then a final
`curl /api/health` before calling it done. Needs `DEPLOY_SSH_KEY` (secret) and
`DEPLOY_DOMAIN` (variable) set in the repository's Actions settings — see the
workflow file's header for exactly what each needs to be.

**If the health check fails, it rolls back automatically** — see
`rollback.sh` below. The workflow run still ends red so the failure is
visible; production just doesn't sit broken while that gets noticed.

## `rollback.sh`

```bash
./rollback.sh                      # back to the last known-good deploy
./rollback.sh <git-ref>            # back to a specific commit or tag
./rollback.sh --with-db-downgrade  # also run one `alembic downgrade -1`
```

Uses the exact same one-at-a-time, healthcheck-gated swap as a forward
deploy — a rollback is not a separate, less-tested code path. **Never
downgrades the database by default.** An Alembic downgrade is frequently
lossy (a dropped column does not come back), and app code one release behind
is usually still compatible with the current schema — the safer default, not
the lazier one. `--with-db-downgrade` runs exactly one `alembic downgrade -1`
when a specific migration is known to be the problem, and it prints a loud
warning with a five-second pause before doing it.

`deploy.yml` calls this automatically on a failed deploy, always without
`--with-db-downgrade` — an automatic rollback that also guesses at
downgrading the schema is a worse failure mode than the one it's recovering
from. Run it with that flag yourself, deliberately, only when you know a
specific migration is at fault.

`.last-good-deploy` (gitignored, lives next to `.env` on the instance) is the
one fact this depends on: the commit SHA of the most recent deploy whose
health check passed. `deploy.yml` writes it after every success; `rollback.sh`
reads it when no ref is given.

## Upkeep

**Trivy's vulnerability database and Semgrep's ruleset go stale.** Nothing
here refreshes them automatically after the first `up`, unlike development
where a `docker compose up` re-warms them every time. `provision.sh` prints a
cron line for this; if you skipped straight to `docker compose up`, add it
yourself:

```cron
0 3 * * 0 cd /path/to/deploy/compose && docker compose -f docker-compose.prod.yml --env-file .env run --rm warm-trivy && docker compose -f docker-compose.prod.yml --env-file .env run --rm warm-semgrep
```

**Moving to another cloud.** Provision a new VM there, point DNS at it once
you're ready to cut over, copy `.env` across (or generate a fresh one — the
database is empty either way unless you also migrate its data), and run
`provision.sh`. Nothing in `docker-compose.prod.yml`, `frontend/Dockerfile` or
`backend/Dockerfile` refers to a specific cloud, which is the property this
whole path exists to keep true.

**Tearing down.** `docker compose -f docker-compose.prod.yml down -v` removes
everything including the named volumes — the database, the sandbox caches, and
Caddy's certificate. The object storage bucket is not touched; delete it
separately if you're done with it.

## Observability

`docker-compose.observability.yml` — Prometheus, Grafana, Loki, Grafana
Alloy (log shipping), node-exporter, and cAdvisor. `provision.sh` starts it
alongside the app stack automatically; nothing here is optional-by-omission
the way `SANDBOX_MAX_CONCURRENT` is, except `GRAFANA_ADMIN_PASSWORD` and
`GRAFANA_VIEWER_PASSWORD`, which `provision.sh` generates the same way it
generates `SECRET_KEY`.

`provision.sh` also creates a second, read-only Grafana account (`viewer`)
once Grafana is up — a plain call against Grafana's own Admin HTTP API,
skipped if that account already exists so re-running `provision.sh` is
still safe. It's the account to hand out when showing someone a dashboard;
the admin login stays yours.

**Static infra, not part of the release cadence.** Started once, left
running — `deploy.sh` and `rollback.sh` never touch it, the same treatment
Postgres and Redis already get. A dashboard/scrape-config change is a manual
`docker compose -f docker-compose.prod.yml -f docker-compose.observability.yml
up -d --force-recreate <service>` on the instance, not something a code push
triggers.

**Reaching it:**

| | |
|---|---|
| Grafana | `https://<your domain>/grafana` — public, proxied through Caddy; sign in as `admin` with `GRAFANA_ADMIN_PASSWORD`, or as `viewer` (read-only, safe to share) with `GRAFANA_VIEWER_PASSWORD` — both from `.env` |
| Prometheus | `http://localhost:9090` via SSH tunnel only — `ssh -L 9090:localhost:9090 <host>` |
| Loki | `http://localhost:3100` via SSH tunnel only — `ssh -L 3100:localhost:3100 <host>` |

Prometheus and Loki are loopback-bound on the instance (`127.0.0.1:9090`,
`127.0.0.1:3100` in the compose file) — neither has authentication of its
own, so neither is reachable from outside the VM by any route, tunnel
aside. Grafana is the only one meant to be public; its own login is the
actual boundary.

**Four dashboards**, provisioned as code under
`observability/grafana/dashboards/*.json` (edit and commit, not click-and-forget
in the UI): API request rate/latency/error-rate, scan queue depth and job
outcomes, per-container + host resource usage, and live log search filtered
by service.

**Retention:** 14 days for both Prometheus and Loki (`--storage.tsdb.retention.time`
in the compose file; `retention_period` in `observability/loki/loki-config.yml`) —
long enough to show a real trend, bounded on disk. `deploy/aws/variables.tf`'s
`root_volume_size_gb` is sized with this assumed.

**Known local-dev-only gap:** cAdvisor's per-container CPU/memory panels
show no data under Docker Desktop (Windows/Mac) — its virtualized VM layer
can't read the real host's Docker overlayfs layer metadata the way a native
Linux Docker install can (`failed to identify the read-write layer ID`, in
`docker logs cadvisor`). Host-level metrics (node-exporter) and everything
else work identically in both places; this is specifically a Docker Desktop
limitation, verified working on the actual Linux VM in `deploy/aws/`.
