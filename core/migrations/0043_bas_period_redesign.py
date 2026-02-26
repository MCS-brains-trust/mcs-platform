"""
Migration for BAS Period-Aware Redesign.
Adds:
  - bas_frequency field to Entity model
  - BASPeriod model for per-period status tracking
"""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0042_merge_description_and_bulkjournal"),
    ]

    operations = [
        # 1. Add bas_frequency to Entity
        migrations.AddField(
            model_name="entity",
            name="bas_frequency",
            field=models.CharField(
                choices=[("quarterly", "Quarterly"), ("monthly", "Monthly")],
                default="quarterly",
                help_text="Determines whether BAS periods are quarterly (4 periods) or monthly (12 periods).",
                max_length=10,
            ),
        ),
        # 2. Create BASPeriod model
        migrations.CreateModel(
            name="BASPeriod",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("period_type", models.CharField(choices=[("quarterly", "Quarterly"), ("monthly", "Monthly")], max_length=10)),
                ("period_number", models.PositiveSmallIntegerField(help_text="1-4 for quarterly, 1-12 for monthly")),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("status", models.CharField(choices=[("empty", "Empty"), ("partial", "Partial"), ("ready", "Ready"), ("lodged", "Lodged")], default="empty", max_length=15)),
                ("lodged_at", models.DateTimeField(blank=True, null=True)),
                ("unlodged_at", models.DateTimeField(blank=True, null=True)),
                ("snapshot_1a", models.DecimalField(blank=True, decimal_places=2, help_text="Snapshot of GST on Sales (label 1A) at time of lodgement", max_digits=12, null=True)),
                ("snapshot_1b", models.DecimalField(blank=True, decimal_places=2, help_text="Snapshot of GST on Purchases (label 1B) at time of lodgement", max_digits=12, null=True)),
                ("snapshot_net", models.DecimalField(blank=True, decimal_places=2, help_text="Snapshot of Net GST position at time of lodgement", max_digits=12, null=True)),
                ("override_reason", models.TextField(blank=True, default="", help_text="Reason provided if lodging with incomplete coverage or warnings")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("financial_year", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bas_periods", to="core.financialyear")),
                ("lodged_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lodged_bas_periods", to=settings.AUTH_USER_MODEL)),
                ("unlodged_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="unlodged_bas_periods", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "BAS Period",
                "verbose_name_plural": "BAS Periods",
                "ordering": ["period_number"],
                "unique_together": {("financial_year", "period_type", "period_number")},
            },
        ),
    ]
