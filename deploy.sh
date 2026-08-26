#!/usr/bin/env bash

set -e

echo "================================"
echo "Starting Arckenites deployment"
echo "================================"

cd /opt/arckenites

echo "Current directory:"
pwd

echo "Fetching latest code..."
git fetch origin main

echo "Resetting to origin/main..."
git reset --hard origin/main

cd /opt/arckenites/backend

echo "Installing dependencies..."
/opt/arckenites/backend/venv/bin/pip install -r requirements.txt

echo "Running database migrations..."
/opt/arckenites/backend/venv/bin/alembic upgrade head

echo "Restarting backend..."
sudo -n systemctl restart arckenites-backend

echo "Checking backend status..."
sudo -n systemctl is-active --quiet arckenites-backend

echo "Running health check..."

for i in {1..10}; do
    if curl -fsS http://127.0.0.1:8000/api/health; then
        echo
        echo "Backend is healthy."
        echo "================================"
        echo "Deployment successful"
        echo "================================"
        exit 0
    fi

    echo "Backend not ready yet. Waiting 2 seconds..."
    sleep 2
done

echo "ERROR: Backend failed health check after 20 seconds."
sudo journalctl -u arckenites-backend -n 30 --no-pager
exit 1
