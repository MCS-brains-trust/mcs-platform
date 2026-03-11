import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0075_trialbalanceline_eva_flags"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EvaFindingSuppression",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("fingerprint", models.CharField(max_length=64)),
                ("rule_category", models.CharField(max_length=100)),
                ("suppressed_at", models.DateTimeField(auto_now_add=True)),
                ("accountant_note", models.TextField(blank=True)),
                (
                    "financial_year",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="suppressed_findings",
                        to="core.financialyear",
                    ),
                ),
                (
                    "suppressed_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "unique_together": {("financial_year", "fingerprint")},
            },
        ),
    ]
