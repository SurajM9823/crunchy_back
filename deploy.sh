#!/bin/bash
set -e

APP_DIR="/opt/crunchy_back"
VENV="$APP_DIR/.venv"

echo "=========================================="
echo "🚀 Crunchy Bag Backend Deployment"
echo "=========================================="

echo "📥 [1/5] Pulling latest code from origin/main..."
cd "$APP_DIR"
git pull origin main

echo "📦 [2/5] Installing/updating requirements..."
"$VENV/bin/pip" install -q -r requirements.txt

echo "🗄️  [3/5] Running database migrations..."
"$VENV/bin/python3" manage.py migrate --noinput

echo "🎨 [4/5] Collecting static files..."
"$VENV/bin/python3" manage.py collectstatic --noinput

echo "🔄 [5/5] Restarting Daphne and Celery services..."
sudo systemctl restart crunchy-daphne crunchy-celery crunchy-celerybeat

echo ""
echo "🔍 Checking service status:"
services=("crunchy-daphne" "crunchy-celery" "crunchy-celerybeat")
for s in "${services[@]}"; do
    if systemctl is-active --quiet "$s"; then
        echo "  ✅ $s: active (running)"
    else
        echo "  ❌ $s: FAILED"
        exit 1
    fi
done

echo ""
echo "✨ Deployment & restart completed successfully!"
