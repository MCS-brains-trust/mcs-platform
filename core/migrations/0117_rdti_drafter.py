"""
Migration: 0117_rdti_drafter
Adds all R&D Tax Incentive Drafter models to the database.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0116_clientaccountmapping_beneficiary_officer"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # RdtiApplication
        migrations.CreateModel(
            name="RdtiApplication",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(
                    choices=[
                        ("intake", "Intake in Progress"),
                        ("drafting", "Drafting"),
                        ("review", "Under Review"),
                        ("ready", "Ready to Lodge"),
                        ("lodged", "Lodged"),
                    ],
                    default="intake",
                    max_length=20,
                )),
                ("abn", models.CharField(blank=True, max_length=11, verbose_name="ABN")),
                ("acn", models.CharField(blank=True, max_length=9, verbose_name="ACN")),
                ("company_name", models.CharField(blank=True, max_length=255)),
                ("contact_name", models.CharField(blank=True, max_length=255)),
                ("contact_email", models.EmailField(blank=True)),
                ("contact_phone", models.CharField(blank=True, max_length=50)),
                ("aggregated_turnover", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                ("employee_count", models.PositiveIntegerField(blank=True, null=True)),
                ("anzsic_division", models.CharField(blank=True, max_length=2, verbose_name="ANZSIC Division")),
                ("anzsic_code", models.CharField(blank=True, max_length=10)),
                ("ip_owned_by_entity", models.BooleanField(blank=True, null=True)),
                ("entity_bears_financial_burden", models.BooleanField(blank=True, null=True)),
                ("entity_controls_activities", models.BooleanField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("lodged_at", models.DateTimeField(blank=True, null=True)),
                ("financial_year", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="rdti_application",
                    to="core.financialyear",
                )),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="rdti_applications_created",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"verbose_name": "RDTI Application", "verbose_name_plural": "RDTI Applications", "ordering": ["-created_at"]},
        ),
        # RdtiProject
        migrations.CreateModel(
            name="RdtiProject",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("project_title", models.CharField(max_length=255)),
                ("project_start_date", models.DateField(blank=True, null=True)),
                ("project_end_date", models.DateField(blank=True, null=True)),
                ("anzsrc_division", models.CharField(blank=True, max_length=2, verbose_name="ANZSRC Division (Field of Research)")),
                ("anzsrc_code", models.CharField(blank=True, max_length=10)),
                ("objectives", models.TextField(blank=True)),
                ("documents_kept", models.TextField(blank=True)),
                ("plant_and_facilities", models.TextField(blank=True)),
                ("beneficiary_description", models.TextField(blank=True)),
                ("intake_business_problem", models.TextField(blank=True)),
                ("intake_existing_knowledge", models.TextField(blank=True)),
                ("intake_uncertainty", models.TextField(blank=True)),
                ("intake_who_could_have_known", models.TextField(blank=True)),
                ("intake_expenditure_estimate", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("application", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="projects",
                    to="core.rdtiapplication",
                )),
            ],
            options={"verbose_name": "RDTI Project", "verbose_name_plural": "RDTI Projects", "ordering": ["created_at"]},
        ),
        # RdtiCoreActivity
        migrations.CreateModel(
            name="RdtiCoreActivity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("activity_title", models.CharField(max_length=255)),
                ("activity_start_date", models.DateField(blank=True, null=True)),
                ("activity_end_date", models.DateField(blank=True, null=True)),
                ("performed_by", models.CharField(
                    choices=[
                        ("entity", "The R&D entity itself"),
                        ("on_behalf", "On behalf of another entity"),
                        ("jointly", "Jointly with another entity"),
                    ],
                    default="entity",
                    max_length=20,
                )),
                ("description", models.TextField(blank=True)),
                ("outcome_not_known_in_advance", models.TextField(blank=True)),
                ("competent_professional", models.TextField(blank=True)),
                ("hypothesis", models.TextField(blank=True)),
                ("experiment", models.TextField(blank=True)),
                ("evaluation_method", models.TextField(blank=True)),
                ("conclusions", models.TextField(blank=True)),
                ("new_knowledge", models.TextField(blank=True)),
                ("evidence_kept", models.JSONField(blank=True, default=list)),
                ("sources_investigated", models.JSONField(blank=True, default=list)),
                ("intake_technical_question", models.TextField(blank=True)),
                ("intake_prior_search", models.TextField(blank=True)),
                ("intake_why_unpredictable", models.TextField(blank=True)),
                ("intake_hypothesis_raw", models.TextField(blank=True)),
                ("intake_experiments_run", models.TextField(blank=True)),
                ("intake_measurement", models.TextField(blank=True)),
                ("intake_learnings", models.TextField(blank=True)),
                ("intake_records_kept", models.TextField(blank=True)),
                ("draft_complete", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("application", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="core_activities",
                    to="core.rdtiapplication",
                )),
                ("project", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="core_activities",
                    to="core.rdtiproject",
                )),
            ],
            options={"verbose_name": "RDTI Core Activity", "verbose_name_plural": "RDTI Core Activities", "ordering": ["created_at"]},
        ),
        # RdtiSupportingActivity
        migrations.CreateModel(
            name="RdtiSupportingActivity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("activity_title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("direct_relation", models.TextField(blank=True)),
                ("intake_description", models.TextField(blank=True)),
                ("intake_relation", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("application", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="supporting_activities",
                    to="core.rdtiapplication",
                )),
                ("core_activity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="supporting_activities",
                    to="core.rdticoreactivity",
                )),
            ],
            options={"verbose_name": "RDTI Supporting Activity", "verbose_name_plural": "RDTI Supporting Activities", "ordering": ["created_at"]},
        ),
        # RdtiExpenditureYear
        migrations.CreateModel(
            name="RdtiExpenditureYear",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("financial_year_label", models.CharField(max_length=20)),
                ("labour_expenditure", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                ("contractor_expenditure", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                ("overhead_expenditure", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                ("other_expenditure", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                ("core_activity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="expenditure_years",
                    to="core.rdticoreactivity",
                )),
            ],
            options={"ordering": ["financial_year_label"], "unique_together": {("core_activity", "financial_year_label")}},
        ),
        # RdtiDraftVersion
        migrations.CreateModel(
            name="RdtiDraftVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("target_type", models.CharField(
                    choices=[
                        ("project", "Project"),
                        ("core_activity", "Core Activity"),
                        ("supporting_activity", "Supporting Activity"),
                    ],
                    max_length=30,
                )),
                ("target_id", models.UUIDField()),
                ("field_name", models.CharField(max_length=100)),
                ("version_number", models.PositiveIntegerField(default=1)),
                ("content", models.TextField()),
                ("char_count", models.PositiveIntegerField(default=0)),
                ("prompt_version", models.CharField(blank=True, max_length=50)),
                ("is_current", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("application", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="draft_versions",
                    to="core.rdtiapplication",
                )),
                ("generated_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="rdti_drafts_generated",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"verbose_name": "RDTI Draft Version", "verbose_name_plural": "RDTI Draft Versions", "ordering": ["-version_number"]},
        ),
        # RdtiFlag
        migrations.CreateModel(
            name="RdtiFlag",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("target_type", models.CharField(max_length=30)),
                ("target_id", models.UUIDField()),
                ("field_name", models.CharField(max_length=100)),
                ("severity", models.CharField(
                    choices=[
                        ("red", "Red — Blocks Submission"),
                        ("amber", "Amber — Review Required"),
                        ("green", "Green — Meets Quality Threshold"),
                    ],
                    max_length=10,
                )),
                ("flag_type", models.CharField(max_length=50)),
                ("message", models.TextField()),
                ("suggestion", models.TextField(blank=True)),
                ("is_resolved", models.BooleanField(default=False)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("application", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="flags",
                    to="core.rdtiapplication",
                )),
            ],
            options={"verbose_name": "RDTI Flag", "verbose_name_plural": "RDTI Flags", "ordering": ["severity", "-created_at"]},
        ),
    ]
