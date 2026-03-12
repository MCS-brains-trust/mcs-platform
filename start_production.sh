#!/bin/bash
# StatementHub — Production Start Script
# Runs with Gunicorn + Celery for 100+ concurrent users
#
# Usage:
#   ./start_production.sh          # Start in foreground (Gunicorn only)
#   ./start_production.sh daemon    # Start as background daemon (all services)
#   ./start_production.sh status    # Check status of all services

set -e

# Activate virtual environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source venv/bin/activate

case "${1:-}" in
    status)
        echo "=== StatementHub Service Status ==="
        echo ""
        echo "Gunicorn:"
        if [ -f /tmp/statementhub.pid ] && kill -0 "$(cat /tmp/statementhub.pid)" 2>/dev/null; then
            echo "  Running (PID: $(cat /tmp/statementhub.pid))"
        else
            echo "  Not running"
        fi
        echo ""
        echo "Redis:"
        systemctl is-active redis-server 2>/dev/null && echo "  Active" || echo "  Inactive"
        echo ""
        echo "Celery Worker:"
        systemctl is-active celery 2>/dev/null && echo "  Active" || echo "  Inactive"
        echo ""
        echo "Celery Beat:"
        systemctl is-active celerybeat 2>/dev/null && echo "  Active" || echo "  Inactive"
        exit 0
        ;;

    daemon)
        # Run migrations
        echo "Running database migrations..."
        python manage.py migrate --noinput

        # Collect static files
        echo "Collecting static files..."
        python manage.py collectstatic --noinput

        # Ensure Celery services are running
        echo "Starting Celery services..."
        systemctl start redis-server celery celerybeat 2>/dev/null || echo "  (Celery systemd services not installed — run deploy/setup_celery.sh)"

        # Start Gunicorn
        echo "Starting Gunicorn (workers: auto, threads: 4)..."
        gunicorn config.wsgi:application \
            --config gunicorn.conf.py \
            --daemon \
            --pid /tmp/statementhub.pid
        echo "StatementHub started as daemon (PID: $(cat /tmp/statementhub.pid))"
        ;;

    *)
        # Run migrations
        echo "Running database migrations..."
        python manage.py migrate --noinput

        # Collect static files
        echo "Collecting static files..."
        python manage.py collectstatic --noinput

        # Start Gunicorn (foreground)
        echo "Starting Gunicorn (workers: auto, threads: 4)..."
        echo "  NOTE: Celery worker and beat should be running as systemd services."
        echo "  Check with: systemctl status celery celerybeat"
        exec gunicorn config.wsgi:application \
            --config gunicorn.conf.py
        ;;
esac
