#!/usr/bin/env bash
# Runs ON THE SERVER (piped over SSH stdin by the deploy workflow).
# App-specific values are hardcoded here on purpose: this script only
# ever deploys this one app, to this one path, on this one server.
set -euo pipefail

APP_DIR="/opt/hubspot-rep-dashboard"
IMAGE="hubspot-rep-dashboard-hubspot-dashboard"   # docker compose v2 default: <project>-<service>
HEALTH_URL="http://127.0.0.1:3010"
HEALTH_ATTEMPTS=10
HEALTH_SLEEP_SECONDS=3

cd "$APP_DIR"

echo "== Tagging currently running image as :previous (if one exists) =="
if docker image inspect "$IMAGE:latest" >/dev/null 2>&1; then
  docker tag "$IMAGE:latest" "$IMAGE:previous"
  HAVE_PREVIOUS=1
else
  echo "No existing :latest image found — this looks like the first deploy."
  HAVE_PREVIOUS=0
fi

echo "== Building new image (current container is not touched yet) =="
if ! docker compose build; then
  echo "BUILD FAILED. Nothing on the server was touched — the currently running container is untouched and still serving traffic."
  exit 1
fi

echo "== Build succeeded — deploying new image =="
docker compose up -d

echo "== Health-checking $HEALTH_URL =="
OK=0
for i in $(seq 1 "$HEALTH_ATTEMPTS"); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || echo "000")
  if [ "$CODE" = "200" ]; then
    OK=1
    break
  fi
  echo "Attempt $i/$HEALTH_ATTEMPTS: got HTTP $CODE, retrying in ${HEALTH_SLEEP_SECONDS}s..."
  sleep "$HEALTH_SLEEP_SECONDS"
done

if [ "$OK" -ne 1 ]; then
  echo "HEALTH CHECK FAILED after $HEALTH_ATTEMPTS attempts."
  if [ "$HAVE_PREVIOUS" -eq 1 ]; then
    echo "Rolling back to the previous image automatically."
    docker tag "$IMAGE:previous" "$IMAGE:latest"
    docker compose up -d --force-recreate
    echo "Rollback complete. Production is back on the previous working version."
    echo "NOTE: this rollback only restarts the previous CONTAINER image. If a database"
    echo "migration or schema change partially applied before this failure, it is NOT"
    echo "undone by this rollback — a human must check the database manually."
  else
    echo "No previous image exists to roll back to (this was the first deploy)."
    echo "Manual intervention is required — the new container is left running for you to debug."
  fi
  exit 1
fi

echo "Deploy succeeded — new version is live and healthy."
