#!/usr/bin/env bash
#
# Deploy Hawkeye from published images.
#
# Run on the deployment host. Pulls the code (for compose files and
# migrations), pulls the images CI published, applies migrations, and restarts.
#
#   ./scripts/deploy.sh              # follow main
#   HAWKEYE_TAG=<sha> ./scripts/deploy.sh   # pin to one build
#
# Migrations run before the new images serve traffic, because a container
# expecting a column that does not exist yet fails in a much more confusing way
# than a migration that has not run.

set -euo pipefail

cd "$(dirname "$0")/.."

FILES=(-f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.ghcr.yml)
export HAWKEYE_TAG="${HAWKEYE_TAG:-latest}"

if [[ ! -f .env ]]; then
  echo "no .env in $(pwd) — deployments need their own configuration" >&2
  exit 1
fi

echo "==> updating source"
git pull --ff-only

echo "==> pulling images (${HAWKEYE_TAG})"
docker compose "${FILES[@]}" pull --quiet api frontend

echo "==> applying migrations"
docker compose "${FILES[@]}" up -d postgres
docker compose "${FILES[@]}" run --rm --no-deps api alembic upgrade head

echo "==> restarting services"
docker compose "${FILES[@]}" up -d

echo "==> waiting for readiness"
for _ in $(seq 30); do
  if docker compose "${FILES[@]}" exec -T api \
      python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/readyz').status==200 else 1)" \
      2>/dev/null; then
    echo "==> ready"
    docker compose "${FILES[@]}" ps --format "table {{.Service}}\t{{.Status}}"
    exit 0
  fi
  sleep 2
done

echo "did not become ready in time; recent api logs:" >&2
docker compose "${FILES[@]}" logs --tail 30 api >&2
exit 1
