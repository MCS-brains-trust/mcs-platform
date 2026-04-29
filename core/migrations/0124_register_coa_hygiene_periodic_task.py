"""
Register a monthly celerybeat task that scans master + per-entity COA rows
for names matching SUSPICIOUS_NAME_REGEX. Detects re-introduction of leaked
client/firm data of the kind that caused the 2026-04-28 trust template
incident. DatabaseScheduler is the active scheduler so periodic tasks live
in django_celery_beat tables.
"""
import json

from django.db import migrations


TASK_NAME = "COA: monthly hygiene scan for suspicious account names"
TASK = "core.check_template_hygiene"


def register_periodic_task(apps, schema_editor):
    try:
        CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return

    monthly, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="4", day_of_week="*", day_of_month="1", month_of_year="*",
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
            "description": (
                "Monthly scan of ChartOfAccount and is_custom=False "
                "EntityChartOfAccount rows for names matching "
                "SUSPICIOUS_NAME_REGEX (bank brands, vehicle models, suburb "
                "names, known leaked tokens). Logs WARNING with sample hits."
            ),
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
        ("core", "0123_register_industry_freshness_periodic_task"),
    ]

    operations = [
        migrations.RunPython(register_periodic_task, unregister_periodic_task),
    ]
