#!/usr/bin/env bash
# Sets up the scan broker for local development — the piece provision.sh's
# prod-only steps around it (domain, TLS, observability, generated secrets)
# do not apply to. Run once, from a checkout of this repository inside a real
# Linux environment: WSL2 running Ubuntu with Docker Engine installed the
# normal apt way, not Docker Desktop's WSL2 backend. Docker Desktop's own
# daemon has no systemd to run this against, and no per-user Docker group to
# reuse — the whole reason this project moved dev off it.
#
# Usage, from a checkout of this repository inside WSL2:
#   cd deploy/compose && ./provision-dev.sh
#   docker compose up -d      # from the repository root, same as always
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "==> Checking for Docker Engine"
if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker Engine (Docker's official convenience script) — the"
    echo "same install this project's EC2 box uses, not Docker Desktop."
    curl -fsSL https://get.docker.com | $SUDO sh
    $SUDO usermod -aG docker "$(whoami)"
    echo "Docker installed. Run 'newgrp docker' (or log out and back in to WSL2)"
    echo "before continuing, so this shell picks up the new group membership."
    exit 0
fi

if ! systemctl is-system-running >/dev/null 2>&1; then
    echo "ERROR: systemd does not appear to be running. In WSL2, add to"
    echo "/etc/wsl.conf on the Windows side:"
    echo "  [boot]"
    echo "  systemd=true"
    echo "then from PowerShell: wsl --shutdown, and reopen your WSL2 terminal." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (the broker runs via 'uv run', same as this project's tests)."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Preparing .env (repository root)"
ROOT_ENV="../../.env"
if [ ! -f "$ROOT_ENV" ]; then
    cp ../../.env.example "$ROOT_ENV"
    echo "Created .env from .env.example."
else
    echo ".env already exists — leaving existing values alone."
fi

echo "==> Installing the scan broker"
$SUDO groupadd -f sentinelops-broker
BROKER_GID="$(getent group sentinelops-broker | cut -d: -f3)"
if grep -q '^BROKER_GID=' "$ROOT_ENV"; then
    sed -i "s/^BROKER_GID=.*/BROKER_GID=${BROKER_GID}/" "$ROOT_ENV"
else
    echo "BROKER_GID=${BROKER_GID}" >>"$ROOT_ENV"
fi
echo "Recorded BROKER_GID=${BROKER_GID} in .env."

REPO_ROOT="$(cd ../.. && pwd)"
sed \
    -e "s#__REPO_ROOT__#${REPO_ROOT}#g" \
    -e "s#__SANDBOX_VOLUME__#sentinelops_worker_data#g" \
    -e "s#__SANDBOX_CACHE_VOLUME__#sentinelops_sandbox_cache#g" \
    -e "s/__DEPLOY_USER__/$(whoami)/g" \
    sentinelops-broker.service | $SUDO tee /etc/systemd/system/sentinelops-broker.service >/dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now sentinelops-broker
echo "Scan broker installed and started (systemctl status sentinelops-broker)."

echo "==> Pre-pulling the sandboxed tool images"
# Not strictly required — the compose warm-* services pull Gitleaks/Trivy/
# Semgrep as a side effect, and Hadolint/Checkov pull on first use — but a
# first real local scan otherwise spends part of its own timeout downloading
# Checkov's ~170MB image.
docker pull hadolint/hadolint:v2.12.0-alpine
docker pull bridgecrew/checkov:3.2.334

echo ""
echo "==> Done. From the repository root: docker compose up -d"
