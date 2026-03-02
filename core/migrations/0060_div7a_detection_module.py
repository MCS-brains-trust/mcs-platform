"""
Migration: Division 7A Detection Module

Creates:
  - Div7AAssessment: one per entity per FY, stores full Div 7A position
  - Div7ACompliance: one per loan per entity, tracks compliance status
"""

import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0059_merge_20260302_2221"),
    ]

    operations = [
        # -----------------------------------------------------------------
        # Div7AAssessment
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="Div7AAssessment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("assessed_at", models.DateTimeField(auto_now=True, help_text="When the assessment last ran")),
                # Position Detection
                ("direct_loan_balance", models.DecimalField(decimal_places=2, default=0, help_text="Total debit balance across director/shareholder loan accounts", max_digits=15)),
                ("direct_loan_accounts", models.JSONField(blank=True, default=list, help_text="Array of {account_code, account_name, balance, py_balance}")),
                ("upe_exposure", models.DecimalField(decimal_places=2, default=0, help_text="Total UPE amount from related trusts", max_digits=15)),
                ("upe_details", models.JSONField(blank=True, default=list, help_text="Array of {trust_entity_id, trust_name, upe_amount, distribution_date, regime}")),
                ("s109e_payments", models.DecimalField(decimal_places=2, default=0, help_text="Total s 109E payments detected", max_digits=15)),
                ("s109e_details", models.JSONField(blank=True, default=list, help_text="Array of {payee, amount, account_code, description}")),
                ("total_exposure", models.DecimalField(decimal_places=2, default=0, help_text="direct_loan_balance + upe_exposure + s109e_payments", max_digits=15)),
                # Compliance Verification
                ("has_complying_agreement", models.BooleanField(default=False, help_text="True if valid LegalDocument exists covering balance")),
                ("agreement_covers_balance", models.BooleanField(default=False, help_text="True if agreement.loan_amount >= total direct balance")),
                ("expected_interest", models.DecimalField(decimal_places=2, default=0, help_text="Calculated benchmark interest for the year", max_digits=15)),
                ("recorded_interest", models.DecimalField(decimal_places=2, default=0, help_text="Actual interest income found in P&L", max_digits=15)),
                ("interest_compliant", models.BooleanField(default=False, help_text="recorded_interest >= expected_interest * 0.95")),
                ("expected_myr", models.DecimalField(decimal_places=2, default=0, help_text="Calculated minimum yearly repayment", max_digits=15)),
                ("actual_repayments", models.DecimalField(blank=True, decimal_places=2, help_text="Credits on loan account during FY", max_digits=15, null=True)),
                ("myr_compliant", models.BooleanField(blank=True, help_text="True if actual_repayments >= expected_myr", null=True)),
                # Escalation & Severity
                ("escalation_required", models.BooleanField(default=False, help_text="True if total_exposure > 200000")),
                ("rules_fired", models.JSONField(blank=True, default=list, help_text="Array of triggered rule IDs")),
                ("overall_severity", models.CharField(choices=[("CRITICAL", "Critical"), ("ADVISORY", "Advisory"), ("CLEAR", "Clear")], default="CLEAR", max_length=10)),
                # Foreign keys
                ("financial_year", models.OneToOneField(help_text="Unique per FY", on_delete=django.db.models.deletion.CASCADE, related_name="div7a_assessment", to="core.financialyear")),
                ("eva_finding", models.ForeignKey(blank=True, help_text="Link to consolidated finding card", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="div7a_assessments", to="core.evafinding")),
            ],
            options={
                "verbose_name": "Div 7A Assessment",
                "verbose_name_plural": "Div 7A Assessments",
                "ordering": ["-assessed_at"],
            },
        ),
        migrations.AddIndex(
            model_name="div7aassessment",
            index=models.Index(fields=["financial_year", "overall_severity"], name="core_div7aa_financi_idx"),
        ),

        # -----------------------------------------------------------------
        # Div7ACompliance
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="Div7ACompliance",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("borrower_name", models.CharField(help_text="Shareholder/associate/trust borrower name", max_length=255)),
                ("loan_amount", models.DecimalField(decimal_places=2, help_text="Original loan amount covered by agreement", max_digits=15)),
                ("loan_start_date", models.DateField(help_text="Commencement date of complying agreement")),
                ("loan_start_year", models.IntegerField(help_text="FY loan commenced (e.g. 2024)")),
                ("loan_term", models.IntegerField(default=7, help_text="7 (unsecured) or 25 (secured)")),
                ("is_secured", models.BooleanField(default=False, help_text="True if secured over real property")),
                ("status", models.CharField(choices=[("COMPLIANT", "Compliant"), ("NON_COMPLIANT", "Non-Compliant"), ("EXPIRED", "Expired"), ("PENDING", "Pending")], default="PENDING", max_length=15)),
                ("last_reviewed", models.DateTimeField(auto_now=True, help_text="Last review date")),
                ("notes", models.TextField(blank=True, default="", help_text="Accountant notes")),
                # Foreign keys
                ("entity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="div7a_compliance_records", to="core.entity")),
                ("borrower_entity", models.ForeignKey(blank=True, help_text="If borrower is another StatementHub entity", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="div7a_as_borrower", to="core.entity")),
                ("agreement_document", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="div7a_compliance_records", to="core.legaldocument")),
            ],
            options={
                "verbose_name": "Div 7A Compliance Record",
                "verbose_name_plural": "Div 7A Compliance Records",
                "ordering": ["entity", "-loan_start_date"],
            },
        ),
        migrations.AddIndex(
            model_name="div7acompliance",
            index=models.Index(fields=["entity", "status"], name="core_div7ac_entity_idx"),
        ),
    ]
