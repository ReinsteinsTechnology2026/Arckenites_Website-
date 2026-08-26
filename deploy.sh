#!/usr/bin/env bash

set -e

APP_DIR="/opt/arckenites"
FRONTEND_DIR="/opt/arckenites/arckenites-orange"
BACKEND_DIR="/opt/arckenites/backend"
VENV="$BACKEND_DIR/venv/bin"

echo "================================"
echo "Starting Arckenites deployment"
echo "================================"

cd "$APP_DIR"

echo "Fetching latest code..."
git fetch origin main

echo "Resetting application repository..."
git reset --hard origin/main

echo "Deploying frontend..."
cd "$FRONTEND_DIR"
git fetch origin main
git reset --hard origin/main

echo "Frontend deployed."

echo "Installing backend dependencies..."
cd "$BACKEND_DIR"
"$VENV/pip" install -r requirements.txt

echo "Running database migrations..."
"$VENV/alembic" upgrade head

echo "Restarting backend..."
sudo -n systemctl restart arckenites-backend

echo "Checking backend status..."
sudo -n systemctl is-active --quiet arckenites-backend

echo "Checking backend health..."

for i in {1..15}; do
    if curl -fsS http://127.0.0.1:8000/api/health; then
        echo
        echo "Backend is healthy."
        break
    fi

    if [ "$i" -eq 15 ]; then
        echo
        echo "ERROR: Backend failed health check."
        sudo journalctl -u arckenites-backend -n 50 --no-pager
        exit 1
    fi

    echo "Backend not ready yet. Waiting 2 seconds..."
    sleep 2
done

echo "Checking frontend..."

if curl -fsS -o /dev/null https://arckenites.com/; then
    echo "Frontend is reachable."
else
    echo "ERROR: Frontend health check failed."
    exit 1
fi

echo "Checking production API..."

if curl -fsS https://arckenites.com/api/health; then
    echo
    echo "Production API is healthy."
else
    echo "ERROR: Production API health check failed."
    exit 1
fi

echo
echo "================================"
echo "Deployment successful"
echo "Frontend: OK"
echo "Backend: OK"
echo "Database migrations: OK"
echo "Production API: OK"
echo "================================"
