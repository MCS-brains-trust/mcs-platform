"""
Migration: 0095_firmsettings

Adds the FirmSettings singleton model which stores firm-wide branding
(logo, name, contact details, disclaimer) used across all generated documents.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0094_governingdocument_chunking"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FirmSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "firm_name",
                    models.CharField(
                        default="MC & S Pty Ltd",
                        help_text=(
                            "Trading name shown on all documents "
                            "(e.g. 'Smith & Jones Chartered Accountants')."
                        ),
                        max_length=255,
                    ),
                ),
                (
                    "firm_legal_name",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Full legal entity name if different from trading name.",
                        max_length=255,
                    ),
                ),
                (
                    "firm_abn",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Australian Business Number (11 digits, no spaces).",
                        max_length=20,
                        verbose_name="ABN",
                    ),
                ),
                (
                    "firm_address_1",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Street address or PO Box.",
                        max_length=255,
                    ),
                ),
                (
                    "firm_address_2",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Suburb, state, and postcode (e.g. 'Dandenong South VIC 3164').",
                        max_length=255,
                    ),
                ),
                (
                    "firm_phone",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Primary phone number shown on documents.",
                        max_length=30,
                    ),
                ),
                (
                    "firm_email",
                    models.EmailField(
                        blank=True,
                        default="",
                        help_text="Primary contact email shown on documents.",
                        max_length=254,
                    ),
                ),
                (
                    "firm_website",
                    models.URLField(
                        blank=True,
                        default="",
                        help_text="Firm website URL (optional).",
                    ),
                ),
                (
                    "logo",
                    models.ImageField(
                        blank=True,
                        help_text=(
                            "Firm logo. Recommended: PNG with transparent background, "
                            "minimum 400 x 150 px, maximum 2 MB. "
                            "This logo will appear on all generated financial statements, "
                            "workpapers, engagement letters, and legal documents."
                        ),
                        null=True,
                        upload_to="firm_branding/",
                    ),
                ),
                (
                    "compilation_report_name",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "Name used in the Compilation Report signatory block. "
                            "Defaults to firm_name if left blank."
                        ),
                        max_length=255,
                    ),
                ),
                (
                    "document_disclaimer",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text=(
                            "Optional disclaimer appended to legal documents. "
                            "Leave blank to use the platform default."
                        ),
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="firm_settings_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Firm Settings",
                "verbose_name_plural": "Firm Settings",
            },
        ),
    ]
