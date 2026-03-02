"""
Migration: GoingConcernAssessment model
"""

import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0060_div7a_detection_module"),
    ]

    operations = [
        migrations.CreateModel(
            name="GoingConcernAssessment",
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
                ("assessed_at", models.DateTimeField(auto_now=True)),
                (
                    "net_assets",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text="Total assets minus total liabilities",
                        max_digits=15,
                    ),
                ),
                (
                    "cash_position",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text="Net cash/bank balance (including overdraft)",
                        max_digits=15,
                    ),
                ),
                (
                    "cy_revenue",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text="Current year total revenue",
                        max_digits=15,
                    ),
                ),
                (
                    "py_revenue",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text="Prior year total revenue",
                        max_digits=15,
                    ),
                ),
                (
                    "revenue_decline_pct",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Percentage decline (null if PY revenue = 0)",
                        max_digits=7,
                        null=True,
                    ),
                ),
                (
                    "cy_net_result",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text="CY profit/loss",
                        max_digits=15,
                    ),
                ),
                (
                    "py_net_result",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text="PY profit/loss",
                        max_digits=15,
                    ),
                ),
                (
                    "working_capital_ratio",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Current assets / current liabilities (null if uncomputable)",
                        max_digits=7,
                        null=True,
                    ),
                ),
                (
                    "director_loan_balance",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text="Net director loan debit (0 if credit)",
                        max_digits=15,
                    ),
                ),
                (
                    "director_extraction_pct",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Director loan / revenue percentage",
                        max_digits=7,
                        null=True,
                    ),
                ),
                (
                    "is_reliant_on_director",
                    models.BooleanField(
                        default=False,
                        help_text="True if cash < 0 but director loan credit is funding operations",
                    ),
                ),
                (
                    "is_startup",
                    models.BooleanField(
                        default=False,
                        help_text="True if entity has < 2 years of financial data",
                    ),
                ),
                (
                    "rules_fired",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="Array of rule IDs that triggered (GC-01 through GC-06)",
                    ),
                ),
                (
                    "overall_severity",
                    models.CharField(
                        default="CLEAR",
                        help_text="CRITICAL / ADVISORY / CLEAR",
                        max_length=20,
                    ),
                ),
                (
                    "financial_year",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="going_concern_assessment",
                        to="core.financialyear",
                    ),
                ),
                (
                    "eva_finding",
                    models.ForeignKey(
                        blank=True,
                        help_text="Link to consolidated finding card",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="going_concern_assessments",
                        to="core.evafinding",
                    ),
                ),
            ],
            options={
                "verbose_name": "Going Concern Assessment",
                "verbose_name_plural": "Going Concern Assessments",
                "ordering": ["-assessed_at"],
            },
        ),
    ]
