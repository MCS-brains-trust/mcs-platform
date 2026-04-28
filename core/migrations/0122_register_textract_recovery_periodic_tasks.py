"""
Register periodic tasks for the Textract OCR recovery sweep + SNS subscription
verifier. DatabaseScheduler is the active celerybeat scheduler so periodic
tasks must live in django_celery_beat tables, not the settings dict.
"""
import json

from django.db import migrations


POLL_TASK_NAME = "Textract: poll stuck OCR jobs"
POLL_TASK = "core.poll_stuck_textract_jobs"
VERIFY_TASK_NAME = "Textract: verify SNS subscription"
VERIFY_TASK = "core.verify_textract_sns_daily"


def register_periodic_tasks(apps, schema_editor):
    try:
        IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
        CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        # django_celery_beat not installed in this environment; skip.
        return

    every_15_min, _ = IntervalSchedule.objects.get_or_create(
        every=15, period="minutes",
    )
    PeriodicTask.objects.update_or_create(
        name=POLL_TASK_NAME,
        defaults={
            "task": POLL_TASK,
            "interval": every_15_min,
            "crontab": None,
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Recover GoverningDocuments stuck in ocr_pending. SNS-fallback.",
        },
    )

    daily_2am, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="2", day_of_week="*", day_of_month="*", month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name=VERIFY_TASK_NAME,
        defaults={
            "task": VERIFY_TASK,
            "crontab": daily_2am,
            "interval": None,
            "enabled": True,
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
            "description": "Verify Textract SNS subscription is still confirmed.",
        },
    )


def unregister_periodic_tasks(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return
    PeriodicTask.objects.filter(name__in=[POLL_TASK_NAME, VERIFY_TASK_NAME]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0121_governing_document_extraction_recovery_fields"),
    ]

    operations = [
        migrations.RunPython(register_periodic_tasks, unregister_periodic_tasks),
    ]
