import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0108_merge_fte_and_fs_templates"),
    ]

    operations = [
        migrations.CreateModel(
            name="StagedImport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider_name", models.CharField(max_length=50)),
                ("import_mode", models.CharField(default="trial_balance", max_length=30)),
                ("as_at_date", models.DateField()),
                ("from_date", models.DateField(blank=True, null=True)),
                ("to_date", models.DateField(blank=True, null=True)),
                ("lines", models.JSONField()),
                ("merge_warnings", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "financial_year",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="staged_import",
                        to="core.financialyear",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
