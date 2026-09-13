#!/usr/bin/env bash
# Deploys the current origin/main to this instance: build, migrate, then roll
# each service one at a time behind its own healthcheck.
#
# Usage, from a checkout of this repository on the instance:
#   ./deploy.sh
#
# .github/workflows/deploy.yml calls exactly this over SSH, and it is
# runnable by hand for the same reason rollback.sh is — the thing CI does
# should be the thing a human can do, not a second implementation that only
# ever runs in Actions.
#
# **This file exists because the sequence must not be fed to bash over
# stdin.** It used to live inline in deploy.yml as `ssh ... bash -s <<'REMOTE'`,
# which is silently broken: `docker compose run` reads stdin, and stdin *is*
# the heredoc carrying the rest of the script, so migrate swallowed every
# line after it — the backend/worker/frontend rolls and the
# .last-good-deploy-candidate write all vanished, and the whole thing still
# exited 0 because nothing had actually failed. It looked like a 17-second
# successful deploy that mysteriously produced no candidate file. Running
# from a file removes the hazard at the root rather than papering over it;
# the `< /dev/null` redirects below are belt-and-braces for the same class of
# bug, since `docker compose run` still attaches stdin wherever it is invoked.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env"

cd ../..
git fetch origin main
git reset --hard origin/main
DEPLOYED_SHA="$(git rev-parse HEAD)"
echo "==> Deploying $DEPLOYED_SHA"
cd deploy/compose

$COMPOSE build

echo "==> Restarting the scan broker"
# The broker (backend/app/broker/, installed by provision.sh as a systemd
# unit — never a compose service) lives in this same checkout, so it needs
# picking up on every deploy the same as the containers do. If a box was
# provisioned before the broker existed, this restart is a no-op the unit
# file itself doesn't exist yet for; provision.sh is still required once.
if systemctl list-unit-files sentinelops-broker.service >/dev/null 2>&1; then
    sudo systemctl restart sentinelops-broker
else
    echo "sentinelops-broker.service is not installed — run provision.sh once first."
fi

echo "==> Ensuring the sandboxed-tool images are present"
# provision.sh pulls these on first setup; a box provisioned before they
# existed (Hadolint and Checkov landed in v0.72) otherwise never gets them
# through a plain deploy, and every Dockerfile/IaC check then reports errored —
# which reads as a *higher* score, since an errored check costs nothing. No-op
# when the images are already local. Gitleaks/Trivy/Semgrep come down as a side
# effect of the warm-* one-shot services, so only these two need pulling here.
# Kept in sync by hand with provision.sh and the IMAGE constants in
# backend/app/scanners/*/tools/{hadolint,checkov}.py.
docker pull hadolint/hadolint:v2.12.0-alpine
docker pull bridgecrew/checkov:3.2.334

echo "==> Migrating (blocks; a failed migration stops the deploy here, before"
echo "    anything user-facing changes)"
$COMPOSE run --rm -T migrate < /dev/null

echo "==> Rolling backend"
$COMPOSE up -d --no-deps --wait backend

echo "==> Rolling worker"
$COMPOSE up -d --no-deps --wait worker

echo "==> Rebuilding the frontend static files"
$COMPOSE run --rm -T frontend-build < /dev/null

echo "==> Rolling frontend"
# Almost always a no-op — see docker-compose.prod.yml's comment on the
# frontend service. Only recreates the container if Caddyfile itself changed
# in this push, which is what keeps the TLS listener up across a deploy.
$COMPOSE up -d --no-deps frontend

docker image prune -f >/dev/null 2>&1 || true

echo "$DEPLOYED_SHA" > .last-good-deploy-candidate
echo "==> Deployed $DEPLOYED_SHA; candidate recorded"
