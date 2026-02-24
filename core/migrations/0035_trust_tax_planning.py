"""
Migration: Trust Tax Planning Worksheet

Adds:
- EntityChartOfAccount: 5 boolean tag fields (non_deductible, non_assessable, cgt, franked_dividend, franking_credit)
- Entity: is_base_rate_entity boolean
- TaxReferenceData model
- TaxPlanningWorksheet model
- TaxPlanningBeneficiaryRow model
- TaxPlanningScenario model
"""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0034_add_entity_chart_of_account"),
    ]

    operations = [
        # --- EntityChartOfAccount: trust tax planning tags ---
        migrations.AddField(
            model_name="entitychartofaccount",
            name="is_non_deductible",
            field=models.BooleanField(
                default=False,
                help_text="Non-deductible expense — added back for trust distributable income",
            ),
        ),
        migrations.AddField(
            model_name="entitychartofaccount",
            name="is_non_assessable",
            field=models.BooleanField(
                default=False,
                help_text="Non-assessable income — deducted for trust distributable income",
            ),
        ),
        migrations.AddField(
            model_name="entitychartofaccount",
            name="is_cgt",
            field=models.BooleanField(
                default=False,
                help_text="Capital gains account — streamed separately in trust distributions",
            ),
        ),
        migrations.AddField(
            model_name="entitychartofaccount",
            name="is_franked_dividend",
            field=models.BooleanField(
                default=False,
                help_text="Franked dividend income — streamed separately in trust distributions",
            ),
        ),
        migrations.AddField(
            model_name="entitychartofaccount",
            name="is_franking_credit",
            field=models.BooleanField(
                default=False,
                help_text="Franking credits account — grossed up in beneficiary calculations",
            ),
        ),
        # --- Entity: base rate entity flag ---
        migrations.AddField(
            model_name="entity",
            name="is_base_rate_entity",
            field=models.BooleanField(
                default=True,
                help_text="Companies only: True = 25% base rate entity, False = 30% non-base rate. Used in trust tax planning.",
            ),
        ),
        # --- TaxReferenceData ---
        migrations.CreateModel(
            name="TaxReferenceData",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("financial_year_label", models.CharField(blank=True, default="", help_text='e.g. "FY2025". Blank = default for all years.', max_length=10)),
                ("key", models.CharField(help_text='e.g. "tax_free_threshold", "bracket_1_rate"', max_length=100)),
                ("value", models.CharField(help_text='Numeric value stored as string, e.g. "18200", "0.19"', max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Tax Reference Data",
                "verbose_name_plural": "Tax Reference Data",
                "ordering": ["financial_year_label", "key"],
                "unique_together": {("financial_year_label", "key")},
            },
        ),
        # --- TaxPlanningWorksheet ---
        migrations.CreateModel(
            name="TaxPlanningWorksheet",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("distributable_income", models.DecimalField(decimal_places=2, default=0, help_text="Calculated from TB on each page load — stored for audit trail", max_digits=15)),
                ("non_deductible_expenses", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("non_assessable_income", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("net_profit_before_distributions", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("capital_gains", models.DecimalField(decimal_places=2, default=0, help_text="Subset of distributable income", max_digits=15)),
                ("franked_dividends", models.DecimalField(decimal_places=2, default=0, help_text="Subset of distributable income", max_digits=15)),
                ("franking_credits", models.DecimalField(decimal_places=2, default=0, help_text="Subset of distributable income", max_digits=15)),
                ("recommendation_notes", models.TextField(blank=True, default="", help_text="Rich text — Section 5 content. Auto-saves on blur.")),
                ("status", models.CharField(choices=[("draft", "Draft"), ("finalised", "Finalised")], default="draft", max_length=20)),
                ("finalised_at", models.DateTimeField(blank=True, null=True)),
                ("last_updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("financial_year", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="tax_planning_worksheet", to="core.financialyear")),
                ("finalised_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="finalised_tax_plans", to=settings.AUTH_USER_MODEL)),
                ("last_updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_tax_plans", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Tax Planning Worksheet",
                "verbose_name_plural": "Tax Planning Worksheets",
            },
        ),
        # --- TaxPlanningBeneficiaryRow ---
        migrations.CreateModel(
            name="TaxPlanningBeneficiaryRow",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("beneficiary_type", models.CharField(choices=[("individual", "Individual"), ("company", "Company"), ("trust", "Trust")], default="individual", help_text="Denormalised from beneficiary at row creation", max_length=20)),
                ("outside_income", models.DecimalField(decimal_places=2, default=0, help_text="Manual entry — default 0", max_digits=15)),
                ("proposed_distribution", models.DecimalField(decimal_places=2, default=0, help_text="Manual entry — must sum to distributable income", max_digits=15)),
                ("grossed_up_franking_credits", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("total_taxable_income", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("gross_tax_payable", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("medicare_levy", models.DecimalField(decimal_places=2, default=0, help_text="Individuals only", max_digits=15)),
                ("lito_offset", models.DecimalField(decimal_places=2, default=0, help_text="Individuals only", max_digits=15)),
                ("franking_credit_offset", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("net_tax_payable", models.DecimalField(decimal_places=2, default=0, help_text="Floored at 0", max_digits=15)),
                ("effective_tax_rate", models.DecimalField(decimal_places=4, default=0, help_text="4 decimal places, displayed as %", max_digits=7)),
                ("company_tax_rate_override", models.DecimalField(blank=True, decimal_places=4, help_text="Set if non-base-rate company (0.30). Null = use 0.25.", max_digits=5, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("worksheet", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="beneficiary_rows", to="core.taxplanningworksheet")),
                ("beneficiary", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tax_planning_rows", to="core.entityofficer")),
            ],
            options={
                "verbose_name": "Tax Planning Beneficiary Row",
                "verbose_name_plural": "Tax Planning Beneficiary Rows",
                "ordering": ["beneficiary__full_name"],
                "unique_together": {("worksheet", "beneficiary")},
            },
        ),
        # --- TaxPlanningScenario ---
        migrations.CreateModel(
            name="TaxPlanningScenario",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("scenario_name", models.CharField(max_length=100)),
                ("distributions", models.JSONField(default=list, help_text='Array of {"beneficiary_id": "uuid", "proposed_amount": 0, "outside_income": 0}')),
                ("total_tax", models.DecimalField(decimal_places=2, default=0, help_text="Total tax payable for this scenario (cached)", max_digits=15)),
                ("total_distributed", models.DecimalField(decimal_places=2, default=0, help_text="Total distributed for this scenario (cached)", max_digits=15)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("financial_year", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tax_planning_scenarios", to="core.financialyear")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_tax_scenarios", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Tax Planning Scenario",
                "verbose_name_plural": "Tax Planning Scenarios",
                "ordering": ["created_at"],
            },
        ),
        # --- Seed FY2025 tax reference data ---
        migrations.RunPython(
            code=lambda apps, schema_editor: _seed_tax_reference_data(apps, schema_editor),
            reverse_code=migrations.RunPython.noop,
        ),
    ]


def _seed_tax_reference_data(apps, schema_editor):
    TaxReferenceData = apps.get_model("core", "TaxReferenceData")
    records = [
        # Individual income tax brackets
        ("FY2025", "tax_free_threshold", "18200", "Tax-free threshold"),
        ("FY2025", "bracket_1_rate", "0.19", "19% on $18,201–$45,000"),
        ("FY2025", "bracket_1_upper", "45000", "Upper limit of 19% bracket"),
        ("FY2025", "bracket_2_rate", "0.325", "32.5% on $45,001–$120,000"),
        ("FY2025", "bracket_2_upper", "120000", "Upper limit of 32.5% bracket"),
        ("FY2025", "bracket_3_rate", "0.37", "37% on $120,001–$180,000"),
        ("FY2025", "bracket_3_upper", "180000", "Upper limit of 37% bracket"),
        ("FY2025", "bracket_4_rate", "0.45", "45% on $180,001+"),
        # Medicare
        ("FY2025", "medicare_levy_rate", "0.02", "Medicare Levy rate"),
        ("FY2025", "medicare_low_income_threshold", "26000", "Below this: reduced Medicare or nil"),
        # LITO
        ("FY2025", "lito_max_offset", "700", "Low Income Tax Offset maximum"),
        ("FY2025", "lito_shade_out_start", "37500", "LITO phase-out starts"),
        ("FY2025", "lito_shade_out_end", "66667", "LITO phase-out ends"),
        # Company rates
        ("FY2025", "company_base_rate", "0.25", "Company base rate (25%)"),
        ("FY2025", "company_non_base_rate", "0.30", "Company non-base rate (30%)"),
        # Trustee default
        ("FY2025", "trustee_default_tax_rate", "0.47", "Rate applied to undistributed trust income"),
    ]
    for fy_label, key, value, desc in records:
        TaxReferenceData.objects.update_or_create(
            financial_year_label=fy_label,
            key=key,
            defaults={"value": value, "description": desc},
        )
