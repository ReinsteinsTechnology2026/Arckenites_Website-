#!/usr/bin/env bash
#
# Production deployment for Arckenites. Runs on the self-hosted runner for
# every push to main (.github/workflows/deploy.yml).
#
# ORDER, AND WHY IT IS SAFE
#   The backend service is stopped BEFORE any file or schema change, and is
#   started only AFTER migrations and the RBAC sync have succeeded. So:
#     - old code never runs against a new schema (it is stopped first), and
#     - new code never runs against an old schema (it starts last).
#   An explicit `systemctl stop` is not undone by a Restart= policy, so the
#   service cannot come back on the new code during the migration window.
#
#   1. fetch origin/main for both repositories         (nothing changed yet)
#   2. stop the backend service                         (no old code serving)
#   3. reset the application and frontend repositories to the fetched commit
#   4. pip install -r requirements.txt
#   5. alembic upgrade head                             (additive migrations)
#   6. seed.py --rbac-only                              (permission catalog)
#   7. start the backend service
#   8. health checks
#
# FAILURE HANDLING
#   No database change is ever reversed automatically, and no migration is
#   downgraded. Recovery depends on where the failure happened:
#     - before step 2:  nothing was changed. The service is untouched.
#     - steps 2-3:      the service is restarted on the code still on disk.
#     - step 3-4:       code is restored to the previously deployed commit,
#                       dependencies are reinstalled from it, and the service
#                       is restarted. No database change had been made.
#     - steps 5-6:      migrations had started, so the service is LEFT STOPPED.
#                       An operator must check the schema before any restart.
#     - steps 7-8:      migrations had finished, so the previous code is NOT
#                       restored (it would run against the new schema). The
#                       service is left running for inspection.
#
# Paths can be overridden with ARCK_* variables for testing. Production uses
# the defaults and sets none of them.
set -Eeuo pipefail

APP_DIR="${ARCK_APP_DIR:-/opt/arckenites}"
FRONTEND_DIR="${ARCK_FRONTEND_DIR:-$APP_DIR/arckenites-orange}"
BACKEND_DIR="${ARCK_BACKEND_DIR:-$APP_DIR/backend}"
VENV="$BACKEND_DIR/venv/bin"
SERVICE="${ARCK_SERVICE:-arckenites-backend}"
HEALTH_URL="${ARCK_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
SITE_URL="${ARCK_SITE_URL:-https://arckenites.com}"

# Where we are, so the failure handler knows what has and has not changed.
PHASE="preflight"
OLD_APP_SHA=""
OLD_FRONTEND_SHA=""

log() { echo "[deploy] $*"; }

wait_for_health() {
    for i in {1..15}; do
        if curl -fsS "$HEALTH_URL"; then
            echo
            return 0
        fi
        log "Backend not ready yet. Waiting 2 seconds..."
        sleep 2
    done
    return 1
}

on_error() {
    local status=$?
    trap - ERR
    log "ERROR: deployment failed during phase '$PHASE' (exit $status)."

    case "$PHASE" in
        preflight)
            log "Nothing was changed. The service was not stopped."
            ;;
        stopping|stopped)
            log "Restarting $SERVICE on the code that is still on disk."
            sudo -n systemctl start "$SERVICE" || log "Could not start $SERVICE. Check it manually."
            ;;
        code)
            log "Restoring the previously deployed code. No database change had been made."
            git -C "$APP_DIR" reset --hard "$OLD_APP_SHA" || log "Could not restore application code."
            git -C "$FRONTEND_DIR" reset --hard "$OLD_FRONTEND_SHA" || log "Could not restore frontend code."
            if (cd "$BACKEND_DIR" && "$VENV/pip" install -r requirements.txt); then
                sudo -n systemctl start "$SERVICE" || log "Could not start $SERVICE. Check it manually."
            else
                log "Dependency restore failed. $SERVICE is left STOPPED."
            fi
            ;;
        migrating)
            log "Migrations had started. $SERVICE is left STOPPED."
            log "Check the schema before any restart: (cd $BACKEND_DIR && $VENV/alembic current)"
            log "Do not downgrade automatically. Decide the next step with the schema state in hand."
            ;;
        *)
            log "Migrations had completed. $SERVICE is left in its current state for inspection."
            log "The previous code is NOT restored, because the schema is now the new one."
            log "Logs: sudo journalctl -u $SERVICE -n 100 --no-pager"
            ;;
    esac
    exit "$status"
}
trap on_error ERR

echo "================================"
echo "Starting Arckenites deployment"
echo "================================"

cd "$APP_DIR"
log "Fetching latest code for both repositories..."
git fetch origin main
NEW_APP_SHA="$(git rev-parse origin/main)"
OLD_APP_SHA="$(git rev-parse HEAD)"

git -C "$FRONTEND_DIR" fetch origin main
NEW_FRONTEND_SHA="$(git -C "$FRONTEND_DIR" rev-parse origin/main)"
OLD_FRONTEND_SHA="$(git -C "$FRONTEND_DIR" rev-parse HEAD)"
log "Deploying application $NEW_APP_SHA (currently $OLD_APP_SHA)"

PHASE="stopping"
log "Stopping $SERVICE so no old code runs during the schema change..."
sudo -n systemctl stop "$SERVICE"
PHASE="stopped"

PHASE="code"
log "Updating application code..."
git -C "$APP_DIR" reset --hard "$NEW_APP_SHA"
log "Updating frontend code..."
git -C "$FRONTEND_DIR" reset --hard "$NEW_FRONTEND_SHA"

log "Installing backend dependencies..."
(cd "$BACKEND_DIR" && "$VENV/pip" install -r requirements.txt)

PHASE="migrating"
log "Running database migrations..."
(cd "$BACKEND_DIR" && "$VENV/alembic" upgrade head)

log "Syncing permission catalog and default role grants..."
(cd "$BACKEND_DIR" && "$VENV/python" seed.py --rbac-only)

PHASE="started"
log "Starting $SERVICE on the new code..."
sudo -n systemctl start "$SERVICE"
systemctl is-active --quiet "$SERVICE"

log "Checking backend health..."
if ! wait_for_health; then
    echo
    log "ERROR: backend failed health check."
    log "The migrations had completed, so the previous code is NOT restored (it would run against the new schema)."
    log "$SERVICE is left running on the new code for inspection. Do not restart the old code."
    journalctl -u "$SERVICE" -n 50 --no-pager || true
    exit 1
fi
log "Backend is healthy."

log "Checking frontend..."
curl -fsS -o /dev/null "$SITE_URL/"
log "Frontend is reachable."

log "Checking production API..."
curl -fsS "$SITE_URL/api/health"
echo
log "Production API is healthy."

echo
echo "================================"
echo "Deployment successful"
echo "Application: $NEW_APP_SHA"
echo "Frontend: OK"
echo "Backend: OK"
echo "Database migrations: OK"
echo "Production API: OK"
echo "================================"
