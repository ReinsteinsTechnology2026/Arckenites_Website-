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
curl -fsS http://127.0.0.1:8000/api/health

echo
echo "================================"
echo "Deployment successful"
echo "================================"
