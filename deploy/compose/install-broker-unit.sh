#!/usr/bin/env bash
# Installs (or reinstalls) the scan broker's systemd unit from the committed
# template, substituting the values provision.sh and deploy.sh both need.
# Shared rather than duplicated in each script on purpose — two copies of this
# sed pipeline drifting apart is exactly how deploy.sh ended up restarting a
# broker whose *installed* unit file was still the pre-fix version, while
# provision.sh (the only thing that reinstalled it) had to be re-run by hand.
#
# Does not touch the `sentinelops-broker` group or BROKER_GID — those are
# provision.sh's job, once, since removing/recreating a group on every deploy
# is unnecessary churn a plain unit-file refresh doesn't need.
#
# Usage: source this from deploy/compose (both callers already cd there).
set -euo pipefail

REPO_ROOT="$(cd ../.. && pwd)"
sed \
    -e "s#__REPO_ROOT__#${REPO_ROOT}#g" \
    -e "s#__SANDBOX_VOLUME__#sentinelops_prod_worker_data#g" \
    -e "s#__SANDBOX_CACHE_VOLUME__#sentinelops_prod_sandbox_cache#g" \
    -e "s/__DEPLOY_USER__/$(sudo whoami)/g" \
    sentinelops-broker.service | sudo tee /etc/systemd/system/sentinelops-broker.service >/dev/null
sudo systemctl daemon-reload
