"""
Migration: Eva AI Compliance Reviewer
- Adds new status choices to FinancialYear (prepared, pending_eva, eva_cleared, eva_error)
- Adds eva_model_override field to FinancialYear
- Creates EvaReview model
- Creates EvaFinding model
"""
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0043_bas_period_redesign"),
    ]

    operations = [
        # Update FinancialYear.status choices (just altering the field)
        migrations.AlterField(
            model_name="financialyear",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("in_review", "In Review"),
                    ("reviewed", "Reviewed"),
                    ("prepared", "Prepared"),
                    ("pending_eva", "Pending Eva Review"),
                    ("eva_cleared", "Eva Cleared"),
                    ("eva_error", "Eva Error"),
                    ("finalised", "Finalised"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
        # Add eva_model_override to FinancialYear
        migrations.AddField(
            model_name="financialyear",
            name="eva_model_override",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Set to 'opus' if manually escalated for deep review",
                max_length=10,
            ),
        ),
        # Create EvaReview model
        migrations.CreateModel(
            name="EvaReview",
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
                ("triggered_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "model_used",
                    models.CharField(
                        choices=[
                            ("haiku", "Haiku (Pre-flight)"),
                            ("sonnet", "Sonnet (Standard)"),
                            ("opus", "Opus (Deep Review)"),
                        ],
                        default="sonnet",
                        max_length=10,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("cleared", "Cleared"),
                            ("findings_raised", "Findings Raised"),
                            ("error", "Error"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("raw_response", models.JSONField(blank=True, default=dict)),
                (
                    "applicable_checks",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="List of check names applicable to this entity type",
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "error_acknowledged_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("is_rerun", models.BooleanField(default=False)),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                (
                    "financial_year",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="eva_reviews",
                        to="core.financialyear",
                    ),
                ),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="triggered_eva_reviews",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "error_acknowledged_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="acknowledged_eva_errors",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-triggered_at"],
                "indexes": [
                    models.Index(
                        fields=["financial_year", "status"],
                        name="core_evarev_financi_idx",
                    ),
                ],
            },
        ),
        # Create EvaFinding model
        migrations.CreateModel(
            name="EvaFinding",
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
                (
                    "check_name",
                    models.CharField(
                        help_text="e.g. division_7a, sgc, ato_benchmarks, trust_distributions",
                        max_length=50,
                    ),
                ),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("critical", "Critical"),
                            ("advisory", "Advisory"),
                        ],
                        default="advisory",
                        max_length=10,
                    ),
                ),
                ("plain_english_explanation", models.TextField()),
                ("recommendation", models.TextField()),
                (
                    "legislation_reference",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "confidence",
                    models.CharField(
                        choices=[
                            ("high", "High"),
                            ("medium", "Medium"),
                            ("low", "Low"),
                        ],
                        default="medium",
                        max_length=10,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("addressed", "Addressed"),
                        ],
                        default="open",
                        max_length=15,
                    ),
                ),
                ("resolution_note", models.TextField(blank=True, default="")),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "eva_review",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="findings",
                        to="core.evareview",
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="resolved_eva_findings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["severity", "check_name"],
                "indexes": [
                    models.Index(
                        fields=["eva_review", "status"],
                        name="core_evafin_eva_rev_idx",
                    ),
                ],
            },
        ),
    ]
