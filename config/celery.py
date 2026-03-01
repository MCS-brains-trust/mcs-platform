"""
Celery application configuration for StatementHub.

Usage:
    $ celery -A config worker -l info
    $ celery -A config beat -l info
"""
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("statementhub")
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in all installed apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Diagnostic task for verifying Celery connectivity."""
    print(f"Request: {self.request!r}")
