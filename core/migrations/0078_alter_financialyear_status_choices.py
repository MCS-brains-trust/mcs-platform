"""
Schema migration: Update FinancialYear.status field choices to the new 4-state enum.

Old choices: draft, in_review, finished, prepared, pending_eva, eva_cleared, eva_error, locked, finalised, reopened
New choices: draft, in_review, finalised, reopened
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0077_migrate_fy_statuses"),
    ]

    operations = [
        migrations.AlterField(
            model_name="financialyear",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("in_review", "In Review"),
                    ("finalised", "Finalised"),
                    ("reopened", "Reopened"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
    ]
