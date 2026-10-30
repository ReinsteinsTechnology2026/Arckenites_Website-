#!/usr/bin/env bash
#
# Production deployment for Arckenites. Runs on the self-hosted runner for
# every push to main (.github/workflows/deploy.yml).
#
# The workflow tests the exact main commit before invoking this script. The
# production checkout must be clean and on main; a dirty checkout is refused.
# Code advances only by fast-forward merge. No reset, stash, or forced checkout
# can discard local work. The API is stopped before the backup and migrations.
# A verified custom-format database backup is created outside the checkout.
# Once migrations start, failure leaves the API stopped for operator review;
# neither schema downgrade nor code rollback runs automatically.
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
READY_URL="${ARCK_READY_URL:-http://127.0.0.1:8000/api/ready}"
SITE_URL="${ARCK_SITE_URL:-https://arckenites.com}"
BACKUP_DIR="${ARCK_BACKUP_DIR:-/srv/arckenites/backups}"
BACKUP_SCRIPT="${ARCK_BACKUP_SCRIPT:-$BACKEND_DIR/maintenance/backup_database.py}"

# Where we are, so the failure handler knows what has and has not changed.
PHASE="preflight"
OLD_APP_SHA=""
OLD_FRONTEND_SHA=""
BACKUP_FILE=""

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
        stopping|stopped|backup)
            log "Restarting $SERVICE on the code that is still on disk."
            sudo -n systemctl start "$SERVICE" || log "Could not start $SERVICE. Check it manually."
            ;;
        code)
            log "Code or dependencies may have changed. $SERVICE is left STOPPED for operator review."
            log "Previous application commit: $OLD_APP_SHA; backup: $BACKUP_FILE"
            ;;
        migrating)
            log "Migrations had started. $SERVICE is left STOPPED."
            log "Check the schema before any restart: (cd $BACKEND_DIR && $VENV/alembic current)"
            log "Verified pre-migration backup: $BACKUP_FILE"
            log "Do not downgrade or restore automatically. Decide with the schema state in hand."
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
[[ "$(git branch --show-current)" == main ]] || { log "Production checkout must be on main."; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || { log "Application checkout has local changes; refusing to overwrite them."; exit 1; }
[[ -d "$BACKEND_DIR" && -x "$VENV/python" && -x "$VENV/pip" ]] || { log "Backend environment is missing."; exit 1; }
[[ -f "$BACKUP_SCRIPT" ]] || { log "Database backup helper is missing."; exit 1; }
command -v pg_dump >/dev/null && command -v pg_restore >/dev/null || { log "PostgreSQL backup tools are missing."; exit 1; }
log "Fetching latest code for both repositories..."
git fetch origin main
NEW_APP_SHA="$(git rev-parse origin/main)"
OLD_APP_SHA="$(git rev-parse HEAD)"

# Only deploy the commit which passed the workflow's quality job. A later
# push to main must not be deployed under the earlier run's green result.
if [[ ! "${ARCK_EXPECTED_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || [[ "$NEW_APP_SHA" != "$ARCK_EXPECTED_SHA" ]]; then
    log "Expected tested commit does not match origin/main; refusing deployment."
    exit 1
fi

APP_GIT_ROOT="$(git -C "$APP_DIR" rev-parse --show-toplevel)"
FRONTEND_GIT_ROOT="$(git -C "$FRONTEND_DIR" rev-parse --show-toplevel)"
FRONTEND_SEPARATE=false
if [[ "$FRONTEND_GIT_ROOT" != "$APP_GIT_ROOT" ]]; then
    FRONTEND_SEPARATE=true
    [[ "$(git -C "$FRONTEND_DIR" branch --show-current)" == main ]] || { log "Frontend checkout must be on main."; exit 1; }
    [[ -z "$(git -C "$FRONTEND_DIR" status --porcelain --untracked-files=all)" ]] || { log "Frontend checkout has local changes; refusing to overwrite them."; exit 1; }
    git -C "$FRONTEND_DIR" fetch origin main
    NEW_FRONTEND_SHA="$(git -C "$FRONTEND_DIR" rev-parse origin/main)"
    OLD_FRONTEND_SHA="$(git -C "$FRONTEND_DIR" rev-parse HEAD)"
    if [[ "$NEW_FRONTEND_SHA" != "$ARCK_EXPECTED_SHA" ]]; then
        log "Separate frontend checkout differs from the tested commit; refusing deployment."
        exit 1
    fi
fi
git -C "$APP_DIR" merge-base --is-ancestor "$OLD_APP_SHA" "$NEW_APP_SHA" || { log "Application update is not a fast-forward."; exit 1; }
if [[ "$FRONTEND_SEPARATE" == true ]]; then
    git -C "$FRONTEND_DIR" merge-base --is-ancestor "$OLD_FRONTEND_SHA" "$NEW_FRONTEND_SHA" || { log "Frontend update is not a fast-forward."; exit 1; }
fi
case "$(realpath -m "$BACKUP_DIR")/" in
    "$(realpath -m "$APP_DIR")/"*) log "Backup directory must be outside the application checkout."; exit 1 ;;
esac
log "Deploying application $NEW_APP_SHA (currently $OLD_APP_SHA)"

PHASE="stopping"
log "Stopping $SERVICE so no old code runs during the schema change..."
sudo -n systemctl stop "$SERVICE"
PHASE="stopped"

PHASE="backup"
umask 077
install -d -m 700 "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/arckenites-$(date -u +%Y%m%dT%H%M%SZ)-${NEW_APP_SHA:0:12}.dump"
log "Creating and verifying the pre-migration database backup..."
(cd "$BACKEND_DIR" && PYTHONPATH="$BACKEND_DIR" "$VENV/python" "$BACKUP_SCRIPT" "$BACKUP_FILE")

PHASE="code"
log "Updating application code..."
git -C "$APP_DIR" merge --ff-only "$NEW_APP_SHA"
log "Updating frontend code..."
if [[ "$FRONTEND_SEPARATE" == true ]]; then
    git -C "$FRONTEND_DIR" merge --ff-only "$NEW_FRONTEND_SHA"
fi

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

log "Checking database readiness..."
if ! curl -fsS "$READY_URL"; then
    log "ERROR: backend cannot reach the database. Service is left running for inspection."
    exit 1
fi
echo
log "Database is reachable."

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
echo "Verified pre-migration backup: $BACKUP_FILE"
echo "Production API: OK"
echo "================================"
