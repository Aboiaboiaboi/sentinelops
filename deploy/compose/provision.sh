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
    # openssl is present on essentially every Linux base image; this is the
    # same command .env.example tells a human to run by hand, just not left
    # for them to forget.
    SECRET_KEY="$(openssl rand -base64 48)"
    POSTGRES_PASSWORD="$(openssl rand -base64 24)"
    # `#` is not in either alphabet, so this delimiter cannot collide with the
    # generated value the way `/` in a base64 string would with the usual `s/.../.../`.
    sed -i "s#^SECRET_KEY=.*#SECRET_KEY=${SECRET_KEY}#" .env
    sed -i "s#^POSTGRES_PASSWORD=.*#POSTGRES_PASSWORD=${POSTGRES_PASSWORD}#" .env
    echo "Generated .env with a fresh SECRET_KEY and POSTGRES_PASSWORD."
else
    echo ".env already exists — leaving it alone."
fi

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

echo ""
echo "==> Done. https://$(grep '^DOMAIN=' .env | cut -d= -f2-) should now be live"
echo "    once DNS and the certificate have both settled — check with:"
echo "      docker compose -f docker-compose.prod.yml logs -f frontend"
echo ""
echo "Trivy's database and Semgrep's rules do not refresh themselves after"
echo "this. Keep them current with a weekly cron entry:"
echo "  0 3 * * 0 cd $SCRIPT_DIR && docker compose -f docker-compose.prod.yml --env-file .env run --rm warm-trivy && docker compose -f docker-compose.prod.yml --env-file .env run --rm warm-semgrep"
