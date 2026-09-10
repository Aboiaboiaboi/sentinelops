# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning is
[Semantic Versioning](https://semver.org/).

**This file starts at the current release.** SentinelOps had 73 tags before
this file existed; the entries below cover the recent, meaningful ones —
earlier history is in `git log` and the repository's own tags, not
reconstructed here.

## [0.72.0] — 2026-09-10

### Added
- Hadolint — Dockerfile linting (unpinned `apt-get install` versions,
  `apt-get upgrade` baked into a layer, missing `--no-install-recommends`,
  `ADD` where `COPY` belongs, `cd` instead of `WORKDIR`, and ShellCheck
  findings in `RUN` lines), sandboxed with no network access. Deliberately
  does not re-report the four rules this project's own Dockerfile checks
  already cover more precisely (Compose images, init wrappers, and — the
  most common real case — a missing `USER` line entirely, which Hadolint's
  own rule only catches when it's an *explicit* `USER root`).
- Checkov — Terraform/infrastructure-as-code misconfiguration (a public S3
  bucket, a security group open to the world), sandboxed with no network
  access and no warm-cache requirement — its policy library ships inside
  the image. Fixes a real gap: a repository deployed purely via Terraform
  used to be scored as having no deployment configuration at all.
- Both tool images are now pre-pulled during provisioning, so a fresh
  server's first real scan doesn't spend part of its timeout downloading
  Checkov's ~170MB image.
- Check count: 31 → 33.

### Changed
- **Category weights re-priced for production-readiness risk**, not left as
  originally set: Deployment 15→17 (now carries IaC misconfiguration, which
  is breach-tier, not hygiene-tier), Scalability 10→14 (in-memory state or
  local-disk uploads mean a second instance is impossible, and at real
  concurrency you need one), Architecture 20→14 (file size, module layout
  and a README govern long-term velocity, not whether the service survives
  real traffic). Security and Reliability were already correctly priced and
  did not move.
- `SCORING_VERSION` v2 → v3. Scores are not comparable across this
  boundary, and scan-to-scan comparison will correctly decline to show a
  delta rather than report a false regression or improvement.
- Sandboxed tool containers per scan: 3 → 5.

### Fixed
- A repository deployed purely via Terraform, with no Dockerfile and no
  Compose/Kubernetes manifest, was scored as having no deployment
  configuration at all and lost the full config penalty. `is_orchestration()`
  now recognises `.tf`/`.tf.json` files directly.

## [0.71.0] — 2026-09-10

Simplified the "two honest limits" copy (README and the `/who-its-for` page)
into plain-language bullets, aimed at a non-technical reader.

## [0.70.0] — 2026-09-10

### Added
- Public marketing pages: `/home` (landing), `/how-it-works`, and
  `/who-its-for`, all reachable logged out.
- Real typography — Space Grotesk (display), IBM Plex Sans (body), JetBrains
  Mono (data/code) — replacing the OS-default font the app had shipped with
  until now.
- A sized `Logo` component, GSAP scroll-triggered animation on the public
  pages, and the full list of all 31 checks published on `/how-it-works`.

## [0.69.0] — 2026-08-30

### Added
- `GET /health/ready` — a real readiness probe (checks the database and
  Redis), separate from the existing shallow `/health` liveness probe.
- An auto-created, read-only Grafana account, so dashboards can be shared
  without handing out the admin password.

### Changed
- Metrics scrape interval and dashboard auto-refresh: 15s → 1s, for a
  near-real-time feel on the observability dashboards.
- README rewritten in plainer, less jargon-heavy language throughout.

### Fixed
- `/metrics` was counting Prometheus's own scrape requests as application
  traffic, producing a misleading flat "1 req/sec" on the API dashboard.
- The bare `/grafana` path (no trailing slash) 404'd instead of redirecting
  to `/grafana/`.

## [0.68.0] — 2026-08-30

### Added
- Full self-hosted observability stack: Prometheus, Grafana, Loki, and
  Grafana Alloy for log shipping, running alongside the app in production.

## [0.67.0] — 2026-08-30

### Added
- Backend `GET /metrics` endpoint (Prometheus text format) — request
  rate/latency, scan queue depth, and scan job outcomes.

### Fixed
- The deploy workflow's hardcoded security group ID pointed at the old
  region after the AWS migration in 0.66.0.

## [0.66.0] — 2026-08-30

### Changed
- **AWS region migration**: `us-east-1` → `ap-south-1` (Mumbai) — closer to
  the actual deployment's users, comparable pricing. Full instance
  replacement; the old region's resources were torn down after verification.
- Instance resized to `c7i-flex.large` (4GB RAM) to make room for the
  observability stack added in 0.68.0.

## [0.65.0] — 2026-08-30

### Removed
- The frontend's fixture-data scaffolding (`lib/fixtures.ts` and the
  `VITE_USE_FIXTURES` mode) — the real backend has served every screen for
  a while, and the fixture path had become dead weight.
