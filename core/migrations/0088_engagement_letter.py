"""
Migration: Add EngagementLetter model.

Stores per-entity, per-financial-year engagement letters for audit trail
purposes.  Roll-forward is blocked until a letter exists for the target year.
"""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0087_pgvector_embedding"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EngagementLetter",
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
                    "file",
                    models.FileField(
                        help_text="Uploaded engagement letter file (PDF or DOCX).",
                        upload_to="engagement_letters/",
                    ),
                ),
                (
                    "original_filename",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                ("file_size_bytes", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("sent", "Sent to Client"),
                            ("signed", "Signed / Executed"),
                            ("superseded", "Superseded"),
                        ],
                        default="draft",
                        max_length=15,
                    ),
                ),
                (
                    "is_current",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this is the current active engagement letter for the year.",
                    ),
                ),
                ("notes", models.TextField(blank=True, default="")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="engagement_letters",
                        to="core.entity",
                    ),
                ),
                (
                    "financial_year",
                    models.ForeignKey(
                        help_text="The financial year this engagement letter covers.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="engagement_letters",
                        to="core.financialyear",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_engagement_letters",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Engagement Letter",
                "verbose_name_plural": "Engagement Letters",
                "ordering": ["-uploaded_at"],
            },
        ),
        migrations.AddIndex(
            model_name="engagementletter",
            index=models.Index(
                fields=["entity", "financial_year"],
                name="core_engage_entity__idx",
            ),
        ),
    ]
