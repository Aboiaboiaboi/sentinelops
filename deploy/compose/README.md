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
