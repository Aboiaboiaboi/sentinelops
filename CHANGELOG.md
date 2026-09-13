# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning is
[Semantic Versioning](https://semver.org/).

**This file starts at the current release.** SentinelOps had 73 tags before
this file existed; the entries below cover the recent, meaningful ones —
earlier history is in `git log` and the repository's own tags, not
reconstructed here.

## [0.80.0] — 2026-09-13

### Changed
- **The worker no longer holds Docker access at all.** A new host-level
  **scan broker** (`backend/app/broker/`) is now the only process that can
  reach `/var/run/docker.sock` — installed as a systemd unit
  (`deploy/compose/sentinelops-broker.service`), deliberately never a Compose
  service, since a broker declared in `docker-compose.yml` would still need
  the real socket mounted into *it*, in a file the scanner reads. The worker
  talks to it over a narrow Unix socket and can ask for exactly one of the
  five pinned tool images, with every `docker run` flag decided by the
  broker, never the caller. Confirmed directly against this repo:
  `deployment.privileged` no longer flags `docker-compose.yml` or
  `docker-compose.prod.yml` — the finding that started this work is gone from
  both. (The observability compose file's unrelated `/:/rootfs:ro` mount for
  metrics collection still flags, at the same flat −2 as before — the score
  is unchanged.)
- **Local development moves off Docker Desktop onto WSL2 + native Ubuntu
  Docker Engine.** Docker Desktop's socket is `root:root`, which is why
  `group_add: ["0"]` ever worked locally in the first place — a real Docker
  Engine install (WSL2, the EC2 box) uses `root:docker` instead, and this was
  the root cause of an earlier session's whole `DOCKER_GID` chase. Moving dev
  onto the same kind of Docker prod runs on removes that class of bug
  entirely, and gives the broker a real systemd to run under locally too.
  New `deploy/compose/provision-dev.sh` sets up the broker for a fresh
  checkout; `provision.sh`/`deploy.sh` do the equivalent on the EC2 box,
  replacing the `DOCKER_GID` step from v0.77 with a `BROKER_GID` one (a new,
  narrow `sentinelops-broker` group — not the host's real `docker` group).
- `SANDBOX_VOLUME`, `SANDBOX_CACHE_VOLUME`, and `SANDBOX_MAX_CONCURRENT` move
  from the worker's environment to the broker's own systemd unit — the
  worker no longer needs to know any of them, only `SANDBOX_ENABLED` and
  where the broker's socket is.

## [0.79.0] — 2026-09-11

### Changed
- cAdvisor no longer runs `privileged: true`. It gets exactly the one host
  device it actually needs (`/dev/kmsg`) instead of every capability and
  device on the box. Its runtime-directory mount is narrowed from all of
  `/var/run` to the single socket it reads. Verified against the real
  service, not assumed: `container_*` series still populate (1533 of them)
  and the container reports healthy.
- `deployment.privileged` ("Container granted host-level access") now
  recognises a mount of the host's runtime directory or its entire root
  filesystem as the same grant as mounting the Docker socket by name —
  `- /var/run:/var/run:ro` and `- /:/rootfs:ro` used to walk straight past
  it. Verified against every bind mount in this repo's own three deployment
  files: the three new hits are all genuine (node-exporter's and cAdvisor's
  root-filesystem mounts, cAdvisor's former runtime-directory mount), zero
  false positives on `/proc`, `/sys`, `/var/lib/docker`, or any port mapping.

### Fixed
- `backend/Dockerfile` pins `git` to an exact version (`1:2.47.3-0+deb13u1`)
  instead of installing it floating — Hadolint's DL3008, and now clean.
  Confirmed by hand that a glob pin (`git=1:2.47.*`) satisfies apt but not
  Hadolint; only the literal exact version does.
- `deployment.privileged` ("Container granted host-level access") used to
  report only the first file it found granting host access, and stop —
  which meant a socket mount in the local dev compose file could silently
  hide the same grant in whatever actually gets deployed, just because it
  happened to be read first. It now names every matching file in one
  finding. Score impact is unchanged (flat −2, same as Checkov's finding is
  capped regardless of how many policies fail) — this is about honesty, not
  a bigger penalty.
- Self-scan: **95/100** (was 94) — the Hadolint finding is gone. The socket
  finding is unchanged in cost but now names all three files that grant it
  (`docker-compose.yml`, `deploy/compose/docker-compose.observability.yml`,
  `deploy/compose/docker-compose.prod.yml`) instead of just the first.

## [0.77.0] — 2026-09-10

### Fixed
- **The production worker could not reach the Docker daemon, so every tool
  check errored and the self-scan read ~98 instead of 94.** The worker runs
  unprivileged and the socket on a Docker Engine host is `root:docker` (a
  non-root gid), but the compose file only granted the root group — which is
  enough on Docker Desktop and nowhere else. `provision.sh` and `deploy.sh`
  now record the socket's gid as `DOCKER_GID` in `.env` and the worker's
  `group_add` uses it.
- When a sandboxed tool check errors, the reason now names the actual cause. It
  previously always said "Set SANDBOX_ENABLED=true" — even on a worker where
  that flag was already set and the real problem was a missing volume or an
  unreachable Docker daemon (which `verify()` had detected at startup and only
  logged). The worker now installs a `NullSandbox` carrying that reason.
- `deploy/compose/deploy.sh` pulls the Hadolint and Checkov images on every
  deploy. Only `provision.sh` did before, so a box provisioned before those
  tools existed never got the images through a normal deploy, and every
  Dockerfile/IaC check silently errored — which raises the score, since an
  errored check costs nothing.

### Added
- A troubleshooting section in `deploy/compose/README.md` for "the self-scan
  scores higher than expected / every tool check is errored".

## [0.76.0] — 2026-09-10

### Changed
- **The check outcome `failed` is now `flagged`.** A check that runs cleanly
  and finds a problem was being reported with the same word people read as
  "the tool broke" — which is what `errored` already means. `flagged` says
  what actually happened: the scan worked, and it found something. Affects the
  `CheckOutcome` enum, the API JSON for `GET /scans/{id}/checks` and
  `/comparison`, the scan-checks summary in the UI, the PDF report, and the
  marketing copy. `scan status` and `category status` are untouched — a scan
  or a category genuinely can fail, and they keep the word.
- Existing scans are migrated (`8bdaf64ccc50`): the `check_results` JSONB on
  every stored scan has its `"failed"` outcomes rewritten to `"flagged"`. The
  migration is reversible.

## [0.75.0] — 2026-09-10

### Changed
- The frozen GCP Terraform moved from `deploy/*.tf` into `deploy/gcp/` and its
  `.tf` files were renamed `.tf.frozen`, so Terraform, Checkov, and this
  project's own deployment scanner all skip it — a reference implementation
  that has not been the live deployment since 2026-08 should not read as live
  infrastructure or generate findings against config nothing applies. `deploy/`
  is now a three-way index (`aws/` live, `gcp/` frozen, `compose/` the app
  layer). Reactivating the GCP path is a one-line rename, documented in
  `deploy/gcp/README.md`.
- Self-scan: Checkov now inspects only `deploy/aws/` — 13 findings instead of
  32. The impact is unchanged (still the capped −3) and the score is still
  94/100; the 19 findings against the retired GCP config are simply gone.

## [0.74.0] — 2026-09-10

### Changed
- The authenticated app and the auth screens now follow the same theme as the
  marketing pages, at a quieter register: mono uppercase section labels, the
  honey-gold accent on score/grade/count figures, hairline dividers in place
  of stacked boxed cards, and `font-display` page titles with tight tracking.
  No oversized headings, drop caps, pull quotes or scroll animation — the app
  stays a tool. Covers the login and signup screens, the app header and
  footer, Dashboard / Project / Scan / Report, the 404, and every shared
  component (findings, checks, the score gauge, the breakdown chart, scan
  comparison, commit context, GitHub connection, project settings).
- Primary buttons (`variant="default"`) now warm to the accent on hover —
  honey-gold fill with near-black text (~10.9:1) — instead of a dimmed blue.
  This is the one place the accent reaches an interactive control, and only
  on hover; the resting state stays blue.
- The app header's logo now links to `/home` everywhere (it linked to
  `/dashboard` in the app shell and was unlinked on the auth screens); a
  "Projects" nav link carries you back into the app.
- `/how-it-works`: the isolation pull quote lost its lone decorative quote
  mark and was reworded to a plainer line.
- `/who-its-for`: the honey-gold drop cap on the lead paragraph was removed.

### Fixed
- The Report page printed the app header — logo, signed-in email, and a Sign
  out button — onto the paper. The header and footer are now `print:hidden`,
  and the print stylesheet's divider colour was darkened so the report's
  hairline rules survive on white paper.

## [0.73.0] — 2026-09-10

### Changed
- The public marketing pages (`/home`, `/how-it-works`, `/who-its-for`) were
  restructured for editorial contrast: asymmetric grids in place of the
  centred flex column that every section shared, oversized `clamp()`
  headings with tight tracking, mono uppercase eyebrow labels, numbered
  section dividers, a drop cap on the "who it's for" lead, and pull quotes
  lifting one line per page out of the body text. Same dark palette, same
  three fonts, same content and scroll animation.

### Added
- One editorial accent colour (`--editorial`, a honey-gold) used only for
  emphasis — section numbers, eyebrows, quote marks, drop caps — never for
  interactive controls, which stay blue so affordance is never ambiguous.
  Verified at ~10.9:1 against the background.

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
