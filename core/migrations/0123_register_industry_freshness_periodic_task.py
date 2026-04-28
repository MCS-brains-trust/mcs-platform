"""
Register a monthly celerybeat task that warns when the ATO BIC fixture is
older than its expected refresh window. DatabaseScheduler is the active
scheduler so periodic tasks must live in django_celery_beat tables.
"""
import json

from django.db import migrations


TASK_NAME = "Industry: check ATO BIC fixture freshness"
TASK = "core.check_industry_data_freshness"


def register_periodic_task(apps, schema_editor):
    try:
        CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return

    monthly, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="3", day_of_week="*", day_of_month="1", month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={
            "task": TASK,
            "crontab": monthly,
            "interval": None,
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Warn if core.industry_codes.__last_checked__ is older than __expected_refresh_days__.",
        },
    )


def unregister_periodic_task(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0122_register_textract_recovery_periodic_tasks"),
    ]

    operations = [
        migrations.RunPython(register_periodic_task, unregister_periodic_task),
    ]
