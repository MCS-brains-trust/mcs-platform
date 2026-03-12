"""
Add database-backed task tracking fields to BASPeriodCommentary.

Replaces the in-memory _commentary_tasks dict that was lost on every
server restart, causing in-progress commentary generation to silently fail.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0085_franking_account_entry"),
    ]

    operations = [
        migrations.AddField(
            model_name="basperiodcommentary",
            name="celery_task_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Celery task ID for tracking generation progress",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="basperiodcommentary",
            name="generation_started_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the background generation task started executing",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="basperiodcommentary",
            name="generation_completed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the background generation task finished (success or failure)",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="basperiodcommentary",
            name="generation_step",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Current step description for progress polling",
                max_length=100,
            ),
        ),
    ]
