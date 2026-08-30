#!/usr/bin/env bash
# Takes a bare Ubuntu VM — any cloud's free-trial box, or a spare machine — to
# a running SentinelOps. The portability claim in 11-phase5-handoff.md made
# concrete: run this on an AWS EC2 trial, an Azure VM trial, a Hetzner box or a
# laptop, and the result is the same, because none of it names a cloud.
#
# What it does NOT do: point a domain at this machine, or fill in a storage
# bucket. Those are yours to decide before this script's last step, which is
# why it stops and tells you to edit .env rather than guessing.
#
# Usage, from a checkout of this repository on the VM:
#   cd deploy/compose && ./provision.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "==> Checking for Docker"
if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker Engine (Docker's official convenience script)."
    curl -fsSL https://get.docker.com | $SUDO sh
    # So `docker` works in this same session without a fresh login, and in
    # every session after. Harmless to re-run if already a member.
    $SUDO usermod -aG docker "$(whoami)"
    echo "Docker installed. If this is the first time, log out and back in"
    echo "(or run 'newgrp docker') before continuing, so this shell picks up"
    echo "the new group membership."
else
    echo "Docker already present: $(docker --version)"
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: 'docker compose' (the plugin, not docker-compose) is not"
    echo "available. get.docker.com installs it as part of Docker Engine on"
    echo "every supported distribution — if this is a minimal or unusual"
    echo "image, install docker-compose-plugin manually and re-run this"
    echo "script." >&2
    exit 1
fi

echo "==> Preparing .env"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example."
else
    echo ".env already exists — leaving existing values alone."
fi

# Fill in any generated secret that's currently empty — whether that's
# because .env was just created above, or because it already existed but
# predates a variable added since (this bit GRAFANA_ADMIN_PASSWORD once
# already: a var added to .env.example after a server's .env was first
# created just sat empty forever, since the old logic here only generated
# secrets for a brand-new file). Same loop handles both cases identically,
# so that can't happen again.
# openssl is present on essentially every Linux base image; this is the same
# command .env.example tells a human to run by hand, just not left for them
# to forget.
for var in SECRET_KEY POSTGRES_PASSWORD GRAFANA_ADMIN_PASSWORD GRAFANA_VIEWER_PASSWORD; do
    if ! grep -q "^${var}=" .env; then
        echo "${var}=" >>.env
    fi
    if [ -z "$(grep "^${var}=" .env | cut -d= -f2-)" ]; then
        length=48
        [ "$var" = "SECRET_KEY" ] || length=24
        # `#` is not in either alphabet, so this delimiter cannot collide
        # with the generated value the way `/` in base64 would with `s/.../.../`.
        sed -i "s#^${var}=.*#${var}=$(openssl rand -base64 "$length")#" .env
        echo "Generated a fresh ${var} into .env."
    fi
done

MISSING=""
for var in DOMAIN STORAGE_BUCKET; do
    value="$(grep "^${var}=" .env | cut -d= -f2-)"
    if [ -z "$value" ]; then
        MISSING="${MISSING} ${var}"
    fi
done

if [ -n "$MISSING" ]; then
    echo ""
    echo "==> Edit deploy/compose/.env before continuing."
    echo "The following have no default and this script will not guess them:"
    for var in $MISSING; do
        echo "  - ${var}"
    done
    echo ""
    echo "DOMAIN needs a DNS A record pointing at this machine's public IP"
    echo "already in place — Caddy requests a certificate for it on first"
    echo "start and will not serve plain HTTP as a fallback (cookie_secure"
    echo "requires HTTPS)."
    echo ""
    echo "Then re-run this script."
    exit 1
fi

echo "==> Building and starting the stack"
# warm-trivy and warm-semgrep are one-shot services in the compose file
# itself — `up` already starts, runs and exits them once, populating the
# cache the sandboxed scan containers need. An earlier version of this script
# ran them a second time here with `docker compose run`, which raced the
# first pass on the same cache volume and made Semgrep's atomic rename fail
# ("mv: can't rename ... No such file or directory") on a clean provision.
# Fixed by trusting the one `up` already did — see the cron line below for
# how the cache is kept current after this.
docker compose -f docker-compose.prod.yml --env-file .env up -d --build

echo "==> Starting the observability stack (Prometheus, Grafana, Loki, Alloy)"
# No --build: every image here is pulled, not built from this repo. Static
# infra — provisioned once here, never touched by deploy.sh/rollback.sh (see
# deploy/compose/README.md's observability section for why).
OBS="docker compose -f docker-compose.prod.yml -f docker-compose.observability.yml --env-file .env"
$OBS up -d

echo "==> Creating a read-only Grafana account, so the admin password never"
echo "    has to be handed out just to show someone a dashboard"
# Waits for Grafana's own (unauthenticated) health endpoint, since a
# just-started container can take a few seconds before it answers at all —
# calling the admin API too early would just fail and this loop turns that
# into a short, bounded wait instead of a one-shot error.
for _ in $(seq 1 30); do
    $OBS exec -T grafana curl -sf http://localhost:3000/api/health >/dev/null 2>&1 && break
    sleep 1
done
GRAFANA_VIEWER_PASSWORD="$(grep '^GRAFANA_VIEWER_PASSWORD=' .env | cut -d= -f2-)"
GRAFANA_ADMIN_PASSWORD="$(grep '^GRAFANA_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
# Idempotent: skip creation if a "viewer" account already exists from an
# earlier provision — /api/users/lookup 404s for "no such user", which -f
# turns into a nonzero exit, so the ! below means "doesn't exist yet".
if ! $OBS exec -T grafana curl -sf -u "admin:${GRAFANA_ADMIN_PASSWORD}" \
    "http://localhost:3000/api/users/lookup?loginOrEmail=viewer" >/dev/null 2>&1; then
    # A user created through this endpoint is a plain org member with the
    # Viewer role by default (verified against a real Grafana 11.4
    # container) — no separate role-assignment call needed.
    $OBS exec -T grafana curl -sf -u "admin:${GRAFANA_ADMIN_PASSWORD}" -X POST \
        http://localhost:3000/api/admin/users \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"Viewer\",\"login\":\"viewer\",\"password\":\"${GRAFANA_VIEWER_PASSWORD}\"}" \
        >/dev/null
    echo "    Created — sign in as 'viewer' with GRAFANA_VIEWER_PASSWORD from .env."
else
    echo "    Already exists — left alone."
fi

echo ""
echo "==> Done. https://$(grep '^DOMAIN=' .env | cut -d= -f2-) should now be live"
echo "    once DNS and the certificate have both settled — check with:"
echo "      docker compose -f docker-compose.prod.yml logs -f frontend"
echo ""
echo "Grafana is at https://$(grep '^DOMAIN=' .env | cut -d= -f2-)/grafana —"
echo "sign in as admin with GRAFANA_ADMIN_PASSWORD, or as 'viewer' with"
echo "GRAFANA_VIEWER_PASSWORD (read-only — safe to hand out) — both in .env."
echo "Prometheus and Loki have no login of their own and are not exposed"
echo "publicly — reach them via an SSH tunnel if needed:"
echo "  ssh -L 9090:localhost:9090 -L 3100:localhost:3100 <this host>"
echo ""
echo "Trivy's database and Semgrep's rules do not refresh themselves after"
echo "this. Keep them current with a weekly cron entry:"
echo "  0 3 * * 0 cd $SCRIPT_DIR && docker compose -f docker-compose.prod.yml --env-file .env run --rm warm-trivy && docker compose -f docker-compose.prod.yml --env-file .env run --rm warm-semgrep"
