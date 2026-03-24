"""
Migration: 0103_familytrustelectoraldocument

Adds the FamilyTrustElectionDocument model — an interactive internal working
document for Family Trust Elections (FTE) and Interposed Entity Elections (IEE).
"""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0102_alter_yearendcommentary_financial_year"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FamilyTrustElectionDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("election_type", models.CharField(
                    blank=True, default="",
                    choices=[
                        ("fte", "Family Trust Election (FTE)"),
                        ("iee", "Interposed Entity Election (IEE) linked to existing FTE"),
                        ("review_only", "Review only — no election to be made this year"),
                        ("other", "Other"),
                    ],
                    max_length=20,
                )),
                ("election_type_other", models.CharField(blank=True, default="", max_length=255)),
                ("income_year", models.CharField(blank=True, default="", help_text="Income year for election, e.g. '2025'", max_length=20)),
                ("reason_for_election", models.TextField(blank=True, default="")),
                ("proposed_test_individual", models.CharField(blank=True, default="", max_length=255)),
                ("test_individual_relationship", models.CharField(blank=True, default="", max_length=255)),
                ("spouse_details", models.CharField(blank=True, default="", max_length=255)),
                ("expected_beneficiaries", models.TextField(blank=True, default="")),
                ("non_family_distributions", models.CharField(blank=True, default="", max_length=5)),
                ("non_family_distribution_details", models.TextField(blank=True, default="")),
                ("checklist_franked_distributions", models.CharField(blank=True, default="", max_length=5)),
                ("checklist_deed_permits_distribution", models.CharField(blank=True, default="", max_length=5)),
                ("checklist_beneficiaries_within_family", models.CharField(blank=True, default="", max_length=5)),
                ("checklist_no_excluded_distributions", models.CharField(blank=True, default="", max_length=5)),
                ("checklist_bucket_company_within_group", models.CharField(blank=True, default="", max_length=5)),
                ("checklist_franking_credit_streaming", models.CharField(blank=True, default="", max_length=5)),
                ("checklist_prior_elections_checked", models.CharField(blank=True, default="", max_length=5)),
                ("date_first_franked_dividend", models.DateField(blank=True, null=True)),
                ("distribution_minutes_prepared_by", models.CharField(blank=True, default="", max_length=255)),
                ("tax_return_prepared_by", models.CharField(blank=True, default="", max_length=255)),
                ("election_lodgment_year_ended", models.CharField(blank=True, default="", max_length=20)),
                ("further_action_required", models.TextField(blank=True, default="")),
                ("risk_notes", models.TextField(blank=True, default="")),
                ("deed_legal_issues", models.TextField(blank=True, default="")),
                ("return_disclosure_references", models.TextField(blank=True, default="")),
                ("adv_trust_deed_reviewed", models.BooleanField(default=False)),
                ("adv_election_year_confirmed", models.BooleanField(default=False)),
                ("adv_test_individual_confirmed", models.BooleanField(default=False)),
                ("adv_family_group_verified", models.BooleanField(default=False)),
                ("adv_iee_considered", models.BooleanField(default=False)),
                ("adv_workpaper_references_saved", models.BooleanField(default=False)),
                ("adv_client_authority_retained", models.BooleanField(default=False)),
                ("adv_reviewer_signoff", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("entity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="fte_documents",
                    to="core.entity",
                )),
                ("financial_year", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="fte_documents",
                    to="core.financialyear",
                )),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_fte_documents",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("last_saved_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="saved_fte_documents",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Family Trust Election Document",
                "verbose_name_plural": "Family Trust Election Documents",
                "ordering": ["-created_at"],
            },
        ),
    ]
