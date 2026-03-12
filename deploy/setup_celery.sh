#!/bin/bash
# =============================================================================
# StatementHub — Celery Production Setup Script
# Run on the DigitalOcean Droplet as root:
#   bash /opt/statementhub/deploy/setup_celery.sh
# =============================================================================
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$DEPLOY_DIR")"
VENV="$PROJECT_DIR/venv/bin"
LOG_DIR="/var/log/statementhub"

echo "============================================"
echo "  StatementHub — Celery Production Setup"
echo "============================================"
echo ""
echo "Project directory: $PROJECT_DIR"
echo ""

# ─── STEP 1: Install Redis if not present ────────────────────────────────────
echo "[1/7] Checking Redis..."
if command -v redis-server &>/dev/null; then
    echo "  Redis is already installed: $(redis-server --version)"
else
    echo "  Installing Redis..."
    apt-get update -qq
    apt-get install -y --no-install-recommends redis-server
    echo "  Redis installed: $(redis-server --version)"
fi

# Enable and start Redis
systemctl enable redis-server
systemctl start redis-server

# Verify Redis is responding
if redis-cli ping | grep -q "PONG"; then
    echo "  Redis is running and responding: PONG"
else
    echo "  ERROR: Redis is not responding. Check: systemctl status redis-server"
    exit 1
fi
echo ""

# ─── STEP 2: Create log directory ────────────────────────────────────────────
echo "[2/7] Creating log directory..."
mkdir -p "$LOG_DIR"
echo "  Log directory: $LOG_DIR"
echo ""

# ─── STEP 3: Install systemd service files ───────────────────────────────────
echo "[3/7] Installing systemd service files..."
cp "$DEPLOY_DIR/celery.service" /etc/systemd/system/celery.service
cp "$DEPLOY_DIR/celerybeat.service" /etc/systemd/system/celerybeat.service
echo "  Installed: /etc/systemd/system/celery.service"
echo "  Installed: /etc/systemd/system/celerybeat.service"
echo ""

# ─── STEP 4: Reload systemd ──────────────────────────────────────────────────
echo "[4/7] Reloading systemd daemon..."
systemctl daemon-reload
echo "  Done."
echo ""

# ─── STEP 5: Run django-celery-beat migrations ──────────────────────────────
echo "[5/7] Running django-celery-beat migrations..."
cd "$PROJECT_DIR"
"$VENV/python" manage.py migrate django_celery_beat --noinput
echo "  Migrations complete."
echo ""

# ─── STEP 6: Set up Knowledge Brain periodic task ────────────────────────────
echo "[6/7] Setting up Knowledge Brain sync schedule (every 2 hours)..."
"$VENV/python" manage.py shell -c "
from django_celery_beat.models import PeriodicTask, IntervalSchedule
import json

# Create or get the 2-hour interval
schedule, _ = IntervalSchedule.objects.get_or_create(
    every=2,
    period=IntervalSchedule.HOURS,
)

# Create or update the periodic task
task, created = PeriodicTask.objects.update_or_create(
    name='sync-knowledge-brain',
    defaults={
        'task': 'core.sync_knowledge_brain',
        'interval': schedule,
        'kwargs': json.dumps({}),
        'enabled': True,
    },
)
action = 'Created' if created else 'Updated'
print(f'  {action} periodic task: {task.name} (every 2 hours)')
"
echo ""

# ─── STEP 7: Enable and start services ──────────────────────────────────────
echo "[7/7] Enabling and starting Celery services..."
systemctl enable celery celerybeat
systemctl restart celery
systemctl restart celerybeat
echo "  Services enabled and started."
echo ""

# ─── Verify ──────────────────────────────────────────────────────────────────
echo "============================================"
echo "  Verification"
echo "============================================"
echo ""
echo "Redis status:"
systemctl is-active redis-server && echo "  redis-server: active" || echo "  redis-server: INACTIVE"
echo ""
echo "Celery worker status:"
systemctl is-active celery && echo "  celery: active" || echo "  celery: INACTIVE"
echo ""
echo "Celery beat status:"
systemctl is-active celerybeat && echo "  celerybeat: active" || echo "  celerybeat: INACTIVE"
echo ""

# Wait for worker to register
echo "Waiting 5 seconds for worker to register..."
sleep 5

echo ""
echo "Celery worker ping:"
cd "$PROJECT_DIR"
"$VENV/celery" -A config inspect ping 2>&1 || echo "  WARNING: Worker not responding yet. Check: journalctl -u celery -n 50"
echo ""

echo "Registered tasks:"
"$VENV/celery" -A config inspect registered 2>&1 | head -30 || echo "  WARNING: Could not query registered tasks."
echo ""

echo "============================================"
echo "  Setup Complete"
echo "============================================"
echo ""
echo "Log files:"
echo "  Worker: $LOG_DIR/celery-worker.log"
echo "  Beat:   $LOG_DIR/celery-beat.log"
echo ""
echo "Management commands:"
echo "  systemctl status celery        # Worker status"
echo "  systemctl status celerybeat    # Beat status"
echo "  systemctl restart celery       # Restart worker"
echo "  systemctl restart celerybeat   # Restart beat"
echo "  journalctl -u celery -f        # Follow worker logs"
echo "  journalctl -u celerybeat -f    # Follow beat logs"
echo ""
echo "To test task dispatch:"
echo "  cd $PROJECT_DIR && $VENV/python manage.py shell -c \\"
echo "    \"from config.celery import debug_task; debug_task.delay()\""
echo ""
