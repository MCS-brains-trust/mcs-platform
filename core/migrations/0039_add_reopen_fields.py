"""
Add fields to support the "Reopen Finalised Year" feature:
- FinancialYear.reopened_at, reopened_by, reopen_reason
- AuditLog.Action.REOPEN choice
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0038_strip_revenue_leading_zeros"),
    ]

    operations = [
        migrations.AddField(
            model_name="financialyear",
            name="reopened_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Timestamp of the last reopen action",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="reopened_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reopened_years",
                to=settings.AUTH_USER_MODEL,
                help_text="User who last reopened this financial year",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="reopen_reason",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Reason provided for reopening this financial year",
            ),
        ),
        # The AuditLog.Action REOPEN choice is handled at the model level;
        # Django TextChoices don't require a migration for new enum values.
    ]
