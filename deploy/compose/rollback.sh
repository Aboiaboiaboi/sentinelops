#!/usr/bin/env bash
# Rolls the running app back to a previous commit, using the exact same
# one-at-a-time, healthcheck-gated swap deploy.yml uses for a forward deploy
# — a rollback is not a different, less-tested code path.
#
# Usage, from a checkout of this repository on the instance:
#   ./rollback.sh                      # roll back to the last known-good deploy
#   ./rollback.sh <git-ref>            # roll back to a specific commit or tag
#   ./rollback.sh --with-db-downgrade  # also run one `alembic downgrade`
#
# What "last known-good deploy" means: deploy.yml writes the deployed commit
# SHA to .last-good-deploy only after its health check passes. That file is
# therefore always the most recent commit that was actually confirmed
# working — not "one commit back" from wherever HEAD happens to be, which
# would be wrong the moment two deploys in a row both succeed.
#
# Does NOT touch the database by default. Alembic downgrades are frequently
# lossy (a dropped column does not come back), and app code one release
# behind is usually still compatible with the current schema — the two
# together mean "leave the schema alone" is the safer default, not merely
# the lazier one. Pass --with-db-downgrade to run exactly one
# `alembic downgrade -1` when a specific migration is known to be the
# problem; it prints a loud warning and pauses before running.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env"

WITH_DB_DOWNGRADE=0
REF=""
for arg in "$@"; do
    case "$arg" in
        --with-db-downgrade)
            WITH_DB_DOWNGRADE=1
            ;;
        --*)
            echo "Unknown flag: $arg" >&2
            exit 1
            ;;
        *)
            REF="$arg"
            ;;
    esac
done

if [ -z "$REF" ]; then
    if [ ! -f .last-good-deploy ]; then
        echo "No ref given and .last-good-deploy does not exist yet — there is" >&2
        echo "nothing recorded to roll back to. Pass a commit or tag explicitly:" >&2
        echo "  ./rollback.sh <git-ref>" >&2
        exit 1
    fi
    REF="$(cat .last-good-deploy)"
    echo "==> No ref given; rolling back to the last known-good deploy: $REF"
else
    echo "==> Rolling back to $REF"
fi

cd ../..
CURRENT="$(git rev-parse HEAD)"
git fetch origin --quiet
git reset --hard "$REF"
TARGET="$(git rev-parse HEAD)"
echo "    $CURRENT -> $TARGET"
cd deploy/compose

$COMPOSE build

if [ "$WITH_DB_DOWNGRADE" -eq 1 ]; then
    echo ""
    echo "########################################################################"
    echo "# --with-db-downgrade was passed. About to run:"
    echo "#   alembic downgrade -1"
    echo "# against the production database. This can permanently discard data —"
    echo "# a downgrade that drops a column does not recover what was in it."
    echo "# Ctrl-C now to abort. Continuing in 5 seconds."
    echo "########################################################################"
    echo ""
    sleep 5
    # -T and </dev/null for the same reason deploy.sh documents at length:
    # `docker compose run` attaches stdin, which silently eats the rest of a
    # script whenever this is invoked with a script on stdin.
    $COMPOSE run --rm -T migrate alembic downgrade -1 < /dev/null
else
    echo "==> Not touching the database (default). Pass --with-db-downgrade to" \
         "run one 'alembic downgrade -1' if a specific migration is the problem."
fi

echo "==> Rolling backend"
$COMPOSE up -d --no-deps --wait backend

echo "==> Rolling worker"
$COMPOSE up -d --no-deps --wait worker

echo "==> Rebuilding the frontend static files"
$COMPOSE run --rm -T frontend-build < /dev/null

echo "==> Rolling frontend"
# Almost always a no-op — see docker-compose.prod.yml's comment on the
# frontend service. Only recreates the container if Caddyfile itself
# differs at $TARGET, which a rollback essentially never touches.
$COMPOSE up -d --no-deps frontend

docker image prune -f >/dev/null 2>&1 || true

echo "==> Verifying"
DOMAIN="$(grep '^DOMAIN=' .env | cut -d= -f2-)"
ok=0
for i in $(seq 1 10); do
    if curl -sf "https://${DOMAIN}/api/health" >/dev/null; then
        ok=1
        break
    fi
    sleep 3
done

if [ "$ok" -eq 1 ]; then
    echo "$TARGET" > .last-good-deploy
    echo "==> Rollback to $TARGET succeeded and is answering."
else
    echo "==> Rollback to $TARGET completed, but /api/health is still not" >&2
    echo "    answering. This needs a human — rollback.sh will not chain" >&2
    echo "    another rollback attempt on top of this one." >&2
    exit 1
fi
