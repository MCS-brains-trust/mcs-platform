"""
Comprehensive migration for Phases 1-14 of the Master Implementation Spec.

Creates 20 new models and adds ~30 fields to existing models:

NEW MODELS (20):
  - EntityChartOfAccount (per-entity chart of accounts)
  - DocumentTemplate (JSON-driven compliance document templates)
  - EntityRelationship (entity-to-entity links)
  - TaxReferenceData (configurable tax rates/thresholds)
  - TaxPlanningWorksheet (trust tax planning per FY)
  - TaxPlanningBeneficiaryRow (per-beneficiary tax calc)
  - TaxPlanningScenario (saved distribution scenarios)
  - EvaTrustPlanningSession (Eva trust planning chat session)
  - TrustWorkspace (6-stage trust distribution workflow)
  - BeneficiaryProfile (beneficiary tax profile)
  - DistributionScenario (distribution modelling scenarios)
  - Section100AAssessment (Section 100A risk assessment)
  - TrustElectionRecord (FTE/IEE election records)
  - GoverningDocument (trust deeds, constitutions, OCR)
  - LegalDocumentTemplate (Word .docx templates)
  - LegalDocument (generated legal document instances)
  - EvaClientSummary (auto-generated client summaries)
  - DividendEvent (dividend declaration events)
  - DividendShareholderAllocation (per-shareholder dividend)
  - EngagementLetterConfig (engagement letter settings)

FIELD ADDITIONS:
  - Entity: 18 new fields (registration_date through metadata)
  - EntityOfficer: 5 new fields (shares_held through other_income)
  - FinancialYear: 8 new fields (package_assembled through locked_by)
"""

import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0046_rename_reviewed_to_finished"),
    ]

    operations = [
        # =====================================================================
        # ENTITY — 18 new fields
        # =====================================================================
        migrations.AddField(
            model_name="entity",
            name="registration_date",
            field=models.DateField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="entity",
            name="financial_year_end",
            field=models.CharField(
                max_length=5, default="06-30",
                help_text="Month-day format, e.g. 06-30 for June",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="reporting_framework",
            field=models.CharField(
                max_length=20, default="GPFR_tier1",
                choices=[
                    ("GPFR_tier1", "General Purpose (Tier 1)"),
                    ("GPFR_tier2", "General Purpose (Tier 2)"),
                    ("SPFR", "Special Purpose"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="company_size",
            field=models.CharField(
                max_length=20, blank=True,
                choices=[
                    ("small_proprietary", "Small Proprietary"),
                    ("large_proprietary", "Large Proprietary"),
                    ("public", "Public"),
                ],
                help_text="Companies only",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="industry",
            field=models.CharField(
                max_length=30, default="",
                choices=[
                    ("accounting", "Accounting & Tax Services"),
                    ("legal", "Legal Services"),
                    ("consulting", "Management Consulting"),
                    ("it_services", "IT & Technology Services"),
                    ("engineering", "Engineering Services"),
                    ("architecture", "Architecture & Design"),
                    ("financial_services", "Financial Services & Planning"),
                    ("real_estate", "Real Estate & Property Services"),
                    ("marketing", "Marketing & Advertising"),
                    ("professional_other", "Other Professional Services"),
                    ("medical_gp", "Medical — General Practice"),
                    ("medical_specialist", "Medical — Specialist"),
                    ("dental", "Dental"),
                    ("allied_health", "Allied Health (Physio, Chiro, etc.)"),
                    ("pharmacy", "Pharmacy"),
                    ("veterinary", "Veterinary"),
                    ("healthcare_other", "Other Healthcare"),
                    ("construction", "Construction — Builder"),
                    ("electrical", "Electrical Contractor"),
                    ("plumbing", "Plumbing Contractor"),
                    ("trades_other", "Other Trades & Contracting"),
                    ("restaurant", "Restaurant & Cafe"),
                    ("hotel", "Hotel & Accommodation"),
                    ("catering", "Catering"),
                    ("food_manufacturing", "Food Manufacturing"),
                    ("hospitality_other", "Other Hospitality"),
                    ("retail", "Retail — General"),
                    ("ecommerce", "E-Commerce & Online Retail"),
                    ("wholesale", "Wholesale & Distribution"),
                    ("transport", "Transport & Logistics"),
                    ("courier", "Courier & Delivery"),
                    ("agriculture", "Agriculture & Farming"),
                    ("mining", "Mining & Resources"),
                    ("fishing", "Fishing & Aquaculture"),
                    ("manufacturing", "Manufacturing — General"),
                    ("nfp_charity", "Not-for-Profit — Charity"),
                    ("nfp_association", "Not-for-Profit — Association"),
                    ("nfp_other", "Not-for-Profit — Other"),
                    ("education", "Education & Training"),
                    ("childcare", "Childcare"),
                    ("property_investment", "Property Investment"),
                    ("investment", "Investment & Holding Company"),
                    ("smsf_industry", "SMSF / Superannuation"),
                    ("beauty", "Beauty & Personal Care"),
                    ("fitness", "Fitness & Recreation"),
                    ("cleaning", "Cleaning Services"),
                    ("security", "Security Services"),
                    ("other", "Other"),
                ],
                help_text="Industry classification — used by Eva for AI analysis, GST coding, and benchmarking.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="bas_frequency",
            field=models.CharField(
                max_length=10, default="quarterly",
                choices=[("quarterly", "Quarterly"), ("monthly", "Monthly")],
                help_text="Determines whether BAS periods are quarterly (4 periods) or monthly (12 periods).",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="is_base_rate_entity",
            field=models.BooleanField(
                default=True,
                help_text="Companies only: True = 25% base rate entity, False = 30% non-base rate. Used in trust tax planning.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="trustee_name",
            field=models.CharField(
                max_length=255, blank=True, default="",
                help_text="Trustee company name (trusts and SMSFs only)",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="trustee_acn",
            field=models.CharField(
                max_length=9, blank=True, default="",
                verbose_name="Trustee ACN",
                help_text="ACN of the trustee company (trusts and SMSFs only)",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="is_large_proprietary",
            field=models.BooleanField(
                default=False,
                help_text="Companies only: True = large proprietary (s.45A Corporations Act). Affects Director's Declaration wording.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="total_shares_on_issue",
            field=models.PositiveIntegerField(
                null=True, blank=True,
                help_text="Companies only: total ordinary shares on issue. Used for dividend calculations.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="default_engagement_fee",
            field=models.DecimalField(
                max_digits=10, decimal_places=2, null=True, blank=True,
                help_text="Default annual engagement fee (AUD). Pre-populates engagement letter wizard.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="default_engagement_services",
            field=models.JSONField(
                default=list, blank=True,
                help_text="Default services list for engagement letters, e.g. ['Tax Return', 'Financial Statements', 'BAS']",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="deed_date",
            field=models.DateField(
                null=True, blank=True,
                help_text="Date of the operative trust deed or partnership agreement.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="deed_reference",
            field=models.CharField(
                max_length=255, blank=True, default="",
                help_text="Reference number or title of the operative deed.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="vesting_date",
            field=models.DateField(
                null=True, blank=True,
                help_text="Trust vesting date (trusts only). Used for compliance checks.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="appointor",
            field=models.CharField(
                max_length=255, blank=True, default="",
                help_text="Name of the appointor (trusts only).",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="metadata",
            field=models.JSONField(
                default=dict, blank=True,
                help_text="Flexible storage: directors, trustees, partners, registered address, etc.",
            ),
        ),

        # =====================================================================
        # ENTITY OFFICER — 5 new fields
        # =====================================================================
        migrations.AddField(
            model_name="entityofficer",
            name="shares_held",
            field=models.PositiveIntegerField(
                null=True, blank=True,
                help_text="Number of shares held (shareholders/directors of companies only). Used for dividend calculations.",
            ),
        ),
        migrations.AddField(
            model_name="entityofficer",
            name="email",
            field=models.EmailField(
                max_length=254, blank=True, default="",
                help_text="Contact email for this officer. Used for FuseSign and engagement letters.",
            ),
        ),
        migrations.AddField(
            model_name="entityofficer",
            name="tax_residency",
            field=models.CharField(
                max_length=15, default="resident",
                choices=[
                    ("resident", "Australian Resident"),
                    ("non_resident", "Non-Resident"),
                    ("temporary", "Temporary Resident"),
                ],
                help_text="Tax residency status. Affects withholding tax on dividends and trust distributions.",
            ),
        ),
        migrations.AddField(
            model_name="entityofficer",
            name="beneficiary_type",
            field=models.CharField(
                max_length=10, blank=True, default="",
                choices=[
                    ("adult", "Adult Individual"),
                    ("minor", "Minor (Under 18)"),
                    ("company", "Company"),
                    ("trust", "Trust"),
                    ("smsf", "SMSF"),
                ],
                help_text="Type of beneficiary (trust beneficiaries only). Affects distribution modelling and Div 6AA.",
            ),
        ),
        migrations.AddField(
            model_name="entityofficer",
            name="other_income",
            field=models.DecimalField(
                max_digits=12, decimal_places=2, null=True, blank=True,
                help_text="Estimated other taxable income for this beneficiary. Used in trust distribution planning.",
            ),
        ),

        # =====================================================================
        # FINANCIAL YEAR — 8 new fields (reopened_at/by/reason already in 0039)
        # =====================================================================
        migrations.AddField(
            model_name="financialyear",
            name="package_assembled",
            field=models.BooleanField(
                default=False,
                help_text="Whether the client package has been assembled for this FY.",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="package_assembled_at",
            field=models.DateTimeField(
                null=True, blank=True,
                help_text="Timestamp when the client package was assembled.",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="package_assembled_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True,
                related_name="assembled_packages",
                help_text="User who assembled the client package.",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="package_sent_for_signing",
            field=models.BooleanField(
                default=False,
                help_text="Whether the package has been sent for signing via FuseSign.",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="package_sent_at",
            field=models.DateTimeField(
                null=True, blank=True,
                help_text="Timestamp when the package was sent for signing.",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="package_fusesign_id",
            field=models.CharField(
                max_length=255, blank=True, default="",
                help_text="FuseSign envelope ID for the client package.",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="locked_at",
            field=models.DateTimeField(
                null=True, blank=True,
                help_text="Timestamp when Eva cleared and locked this financial year.",
            ),
        ),
        migrations.AddField(
            model_name="financialyear",
            name="locked_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True,
                related_name="locked_years",
                help_text="User (or system) who locked this financial year.",
            ),
        ),

        # =====================================================================
        # NEW MODEL: EntityChartOfAccount
        # =====================================================================
        migrations.CreateModel(
            name="EntityChartOfAccount",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("account_code", models.CharField(max_length=20, help_text='Account code, e.g. "500", "1510"')),
                ("account_name", models.CharField(max_length=255, help_text='Account name, e.g. "Sales"')),
                ("classification", models.CharField(max_length=255, blank=True, default="")),
                ("section", models.CharField(
                    max_length=30,
                    choices=[
                        ("suspense", "Suspense"), ("revenue", "Revenue"),
                        ("cost_of_sales", "Cost of Sales"), ("expenses", "Expenses"),
                        ("assets", "Assets"), ("liabilities", "Liabilities"),
                        ("equity", "Equity"), ("capital_accounts", "Capital Accounts"),
                        ("pl_appropriation", "P&L Appropriation"),
                    ],
                )),
                ("tax_code", models.CharField(max_length=20, blank=True, default="")),
                ("is_active", models.BooleanField(default=True)),
                ("is_custom", models.BooleanField(default=False)),
                ("is_non_deductible", models.BooleanField(default=False)),
                ("is_non_assessable", models.BooleanField(default=False)),
                ("is_cgt", models.BooleanField(default=False)),
                ("is_franked_dividend", models.BooleanField(default=False)),
                ("is_franking_credit", models.BooleanField(default=False)),
                ("display_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("entity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="entity_accounts",
                    to="core.entity",
                )),
                ("maps_to", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="entity_detailed_accounts",
                    to="core.accountmapping",
                )),
            ],
            options={
                "ordering": ["section", "account_code"],
                "unique_together": {("entity", "account_code")},
            },
        ),
        migrations.AddIndex(
            model_name="entitychartofaccount",
            index=models.Index(fields=["entity", "section"], name="core_eca_entity_section_idx"),
        ),
        migrations.AddIndex(
            model_name="entitychartofaccount",
            index=models.Index(fields=["entity", "is_active"], name="core_eca_entity_active_idx"),
        ),

        # =====================================================================
        # NEW MODEL: DocumentTemplate
        # =====================================================================
        migrations.CreateModel(
            name="DocumentTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("document_category", models.CharField(
                    max_length=30,
                    choices=[
                        ("distribution_minutes", "Distribution Minutes"),
                        ("trust_election", "Trust Election (s97)"),
                        ("tax_planning_summary", "Tax Planning Summary"),
                        ("financial_statements", "Financial Statements"),
                        ("beneficiary_statement", "Beneficiary Statement"),
                        ("partner_statement", "Partner Statement"),
                        ("other", "Other"),
                    ],
                )),
                ("entity_type", models.CharField(
                    max_length=20, blank=True,
                    choices=[
                        ("company", "Company"), ("trust", "Trust"),
                        ("partnership", "Partnership"), ("sole_trader", "Sole Trader"),
                        ("smsf", "SMSF"),
                    ],
                )),
                ("description", models.TextField(blank=True)),
                ("structure", models.JSONField(default=dict)),
                ("version", models.PositiveIntegerField(default=1)),
                ("is_active", models.BooleanField(default=True)),
                ("superseded_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="supersedes",
                    to="core.documenttemplate",
                )),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_templates",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["document_category", "entity_type", "-version"],
            },
        ),
        migrations.AddConstraint(
            model_name="documenttemplate",
            constraint=models.UniqueConstraint(
                fields=["document_category", "entity_type", "version"],
                name="unique_template_version",
            ),
        ),

        # =====================================================================
        # NEW MODEL: EntityRelationship
        # =====================================================================
        migrations.CreateModel(
            name="EntityRelationship",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("relationship_type", models.CharField(
                    max_length=50, default="associated_entity",
                    choices=[
                        ("trustee_of", "Trustee of"), ("beneficiary_of", "Beneficiary of"),
                        ("director_of", "Director of"), ("shareholder_of", "Shareholder of"),
                        ("partner_in", "Partner in"), ("parent_entity", "Parent Entity"),
                        ("subsidiary", "Subsidiary"), ("associated_entity", "Associated Entity"),
                        ("other", "Other"),
                    ],
                )),
                ("notes", models.TextField(blank=True)),
                ("from_entity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="relationships_from",
                    to="core.entity",
                )),
                ("to_entity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="relationships_to",
                    to="core.entity",
                )),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["relationship_type", "to_entity"],
                "unique_together": {("from_entity", "to_entity", "relationship_type")},
            },
        ),

        # =====================================================================
        # NEW MODEL: TaxReferenceData
        # =====================================================================
        migrations.CreateModel(
            name="TaxReferenceData",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("financial_year_label", models.CharField(max_length=10, blank=True, default="")),
                ("key", models.CharField(max_length=100)),
                ("value", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["financial_year_label", "key"],
                "unique_together": {("financial_year_label", "key")},
                "verbose_name": "Tax Reference Data",
                "verbose_name_plural": "Tax Reference Data",
            },
        ),

        # =====================================================================
        # NEW MODEL: TaxPlanningWorksheet
        # =====================================================================
        migrations.CreateModel(
            name="TaxPlanningWorksheet",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("distributable_income", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("non_deductible_expenses", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("non_assessable_income", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("net_profit_before_distributions", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("capital_gains", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("franked_dividends", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("franking_credits", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("recommendation_notes", models.TextField(blank=True, default="")),
                ("status", models.CharField(
                    max_length=20, default="draft",
                    choices=[("draft", "Draft"), ("finalised", "Finalised")],
                )),
                ("finalised_at", models.DateTimeField(null=True, blank=True)),
                ("last_updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("financial_year", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="tax_planning_worksheet",
                    to="core.financialyear",
                )),
                ("finalised_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="finalised_tax_plans",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("last_updated_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="updated_tax_plans",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Tax Planning Worksheet",
                "verbose_name_plural": "Tax Planning Worksheets",
            },
        ),

        # =====================================================================
        # NEW MODEL: TaxPlanningBeneficiaryRow
        # =====================================================================
        migrations.CreateModel(
            name="TaxPlanningBeneficiaryRow",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("beneficiary_type", models.CharField(
                    max_length=20, default="individual",
                    choices=[("individual", "Individual"), ("company", "Company"), ("trust", "Trust")],
                )),
                ("outside_income", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("proposed_distribution", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("grossed_up_franking_credits", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("total_taxable_income", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("gross_tax_payable", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("medicare_levy", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("lito_offset", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("franking_credit_offset", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("net_tax_payable", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("effective_tax_rate", models.DecimalField(max_digits=7, decimal_places=4, default=0)),
                ("company_tax_rate_override", models.DecimalField(
                    max_digits=5, decimal_places=4, null=True, blank=True,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("worksheet", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="beneficiary_rows",
                    to="core.taxplanningworksheet",
                )),
                ("beneficiary", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="tax_planning_rows",
                    to="core.entityofficer",
                )),
            ],
            options={
                "ordering": ["beneficiary__full_name"],
                "unique_together": {("worksheet", "beneficiary")},
                "verbose_name": "Tax Planning Beneficiary Row",
                "verbose_name_plural": "Tax Planning Beneficiary Rows",
            },
        ),

        # =====================================================================
        # NEW MODEL: TaxPlanningScenario
        # =====================================================================
        migrations.CreateModel(
            name="TaxPlanningScenario",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("scenario_name", models.CharField(max_length=100)),
                ("distributions", models.JSONField(default=list)),
                ("total_tax", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("total_distributed", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("financial_year", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="tax_planning_scenarios",
                    to="core.financialyear",
                )),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_tax_scenarios",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["created_at"],
                "verbose_name": "Tax Planning Scenario",
                "verbose_name_plural": "Tax Planning Scenarios",
            },
        ),

        # =====================================================================
        # NEW MODEL: EvaTrustPlanningSession
        # =====================================================================
        migrations.CreateModel(
            name="EvaTrustPlanningSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("triggered_at", models.DateTimeField(auto_now_add=True)),
                ("net_distributable_income", models.DecimalField(
                    max_digits=15, decimal_places=2, null=True, blank=True,
                )),
                ("beneficiary_incomes_provided", models.BooleanField(default=False)),
                ("recommended_distribution", models.JSONField(default=dict, blank=True)),
                ("final_distribution", models.JSONField(default=dict, blank=True)),
                ("resolution_pre_populated", models.BooleanField(default=False)),
                ("completed_at", models.DateTimeField(null=True, blank=True)),
                ("conversation", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="trust_planning_sessions",
                    to="core.evaconversation",
                )),
                ("financial_year", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="trust_planning_sessions",
                    to="core.financialyear",
                )),
                ("triggered_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="triggered_trust_planning_sessions",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-triggered_at"],
            },
        ),

        # =====================================================================
        # NEW MODEL: TrustWorkspace
        # =====================================================================
        migrations.CreateModel(
            name="TrustWorkspace",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("stage_1_status", models.CharField(max_length=15, default="not_started",
                    choices=[("not_started", "Not Started"), ("in_progress", "In Progress"), ("completed", "Completed")])),
                ("stage_2_status", models.CharField(max_length=15, default="not_started",
                    choices=[("not_started", "Not Started"), ("in_progress", "In Progress"), ("completed", "Completed")])),
                ("stage_3_status", models.CharField(max_length=15, default="not_started",
                    choices=[("not_started", "Not Started"), ("in_progress", "In Progress"), ("completed", "Completed")])),
                ("stage_4_status", models.CharField(max_length=15, default="not_started",
                    choices=[("not_started", "Not Started"), ("in_progress", "In Progress"), ("completed", "Completed")])),
                ("stage_5_status", models.CharField(max_length=15, default="not_started",
                    choices=[("not_started", "Not Started"), ("in_progress", "In Progress"), ("completed", "Completed")])),
                ("stage_6_status", models.CharField(max_length=15, default="not_started",
                    choices=[("not_started", "Not Started"), ("in_progress", "In Progress"), ("completed", "Completed")])),
                ("net_distributable_income", models.DecimalField(
                    max_digits=15, decimal_places=2, null=True, blank=True,
                )),
                ("income_streams", models.JSONField(default=dict, blank=True)),
                ("section_100a_overall_risk", models.CharField(
                    max_length=10, blank=True, default="",
                    choices=[("green", "Green"), ("amber", "Amber"), ("red", "Red")],
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("financial_year", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="trust_workspace",
                    to="core.financialyear",
                )),
            ],
        ),

        # =====================================================================
        # NEW MODEL: DistributionScenario (must be before TrustWorkspace FK)
        # =====================================================================
        migrations.CreateModel(
            name="DistributionScenario",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=100, default="Scenario 1")),
                ("allocations", models.JSONField(default=dict, blank=True)),
                ("total_tax", models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)),
                ("tax_saved_vs_equal", models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)),
                ("is_confirmed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("trust_workspace", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="scenarios",
                    to="core.trustworkspace",
                )),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),

        # Add confirmed_scenario FK to TrustWorkspace
        migrations.AddField(
            model_name="trustworkspace",
            name="confirmed_scenario",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="core.distributionscenario",
            ),
        ),

        # =====================================================================
        # NEW MODEL: BeneficiaryProfile
        # =====================================================================
        migrations.CreateModel(
            name="BeneficiaryProfile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("beneficiary_type", models.CharField(
                    max_length=10, default="adult",
                    choices=[
                        ("adult", "Adult Individual"), ("minor", "Minor"),
                        ("company", "Company"), ("trust", "Trust"), ("smsf", "SMSF"),
                    ],
                )),
                ("other_income", models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)),
                ("marginal_rate", models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)),
                ("bracket_remaining", models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)),
                ("franking_surplus", models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)),
                ("include_in_distribution", models.BooleanField(default=True)),
                ("exclusion_reason", models.TextField(blank=True, default="")),
                ("tax_residency", models.CharField(max_length=20, blank=True, default="AU")),
                ("trust_workspace", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="beneficiary_profiles",
                    to="core.trustworkspace",
                )),
                ("beneficiary", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="trust_beneficiary_profiles",
                    to="core.entityofficer",
                )),
            ],
            options={
                "ordering": ["beneficiary__full_name"],
                "unique_together": {("trust_workspace", "beneficiary")},
            },
        ),

        # =====================================================================
        # NEW MODEL: Section100AAssessment
        # =====================================================================
        migrations.CreateModel(
            name="Section100AAssessment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("q1", models.CharField(max_length=10, blank=True, default="",
                    choices=[("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")])),
                ("q2", models.CharField(max_length=10, blank=True, default="",
                    choices=[("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")])),
                ("q3", models.CharField(max_length=10, blank=True, default="",
                    choices=[("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")])),
                ("q4", models.CharField(max_length=10, blank=True, default="",
                    choices=[("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")])),
                ("q5", models.CharField(max_length=10, blank=True, default="",
                    choices=[("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")])),
                ("q6", models.CharField(max_length=10, blank=True, default="",
                    choices=[("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")])),
                ("q7", models.CharField(max_length=10, blank=True, default="",
                    choices=[("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")])),
                ("q8", models.CharField(max_length=10, blank=True, default="",
                    choices=[("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")])),
                ("risk_rating", models.CharField(max_length=10, blank=True, default="",
                    choices=[("green", "Green"), ("amber", "Amber"), ("red", "Red")])),
                ("resolution_strategy", models.TextField(blank=True, default="")),
                ("reviewed_at", models.DateTimeField(null=True, blank=True)),
                ("trust_workspace", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="section_100a_assessments",
                    to="core.trustworkspace",
                )),
                ("beneficiary", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="section_100a_assessments",
                    to="core.entityofficer",
                )),
                ("reviewed_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="reviewed_100a_assessments",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "unique_together": {("trust_workspace", "beneficiary")},
            },
        ),

        # =====================================================================
        # NEW MODEL: TrustElectionRecord
        # =====================================================================
        migrations.CreateModel(
            name="TrustElectionRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("election_type", models.CharField(
                    max_length=5,
                    choices=[("fte", "Family Trust Election"), ("iee", "Interposed Entity Election")],
                )),
                ("status", models.CharField(
                    max_length=25, default="not_applicable",
                    choices=[
                        ("in_place", "In Place"), ("not_in_place", "Not In Place"),
                        ("required_not_yet_made", "Required — Not Yet Made"),
                        ("not_applicable", "Not Applicable"),
                    ],
                )),
                ("effective_date", models.DateField(null=True, blank=True)),
                ("election_document", models.FileField(upload_to="trust_elections/", blank=True)),
                ("confirmed_at", models.DateTimeField(null=True, blank=True)),
                ("trust_workspace", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="election_records",
                    to="core.trustworkspace",
                )),
                ("test_individual", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="trust_election_test_individual",
                    to="core.entityofficer",
                )),
                ("related_entity", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="trust_election_related",
                    to="core.entity",
                )),
                ("confirmed_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="confirmed_trust_elections",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "unique_together": {("trust_workspace", "election_type")},
            },
        ),

        # =====================================================================
        # NEW MODEL: GoverningDocument
        # =====================================================================
        migrations.CreateModel(
            name="GoverningDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("document_type", models.CharField(
                    max_length=30,
                    choices=[
                        ("trust_deed", "Trust Deed"), ("company_constitution", "Company Constitution"),
                        ("partnership_agreement", "Partnership Agreement"), ("smsf_deed", "SMSF Deed"),
                        ("amendment", "Amendment"), ("supplementary", "Supplementary Document"),
                    ],
                )),
                ("is_primary", models.BooleanField(default=False)),
                ("file", models.FileField(upload_to="governing_documents/")),
                ("original_filename", models.CharField(max_length=500, blank=True, default="")),
                ("file_size_bytes", models.PositiveIntegerField(default=0)),
                ("document_date", models.DateField(null=True, blank=True)),
                ("description", models.TextField(blank=True, default="")),
                ("status", models.CharField(
                    max_length=10, default="active",
                    choices=[("active", "Active"), ("archived", "Archived")],
                )),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("archived_at", models.DateTimeField(null=True, blank=True)),
                ("extracted_text", models.TextField(blank=True, default="")),
                ("extraction_status", models.CharField(
                    max_length=30, default="pending",
                    choices=[
                        ("pending", "Pending"), ("completed", "Completed"),
                        ("completed_with_warnings", "Completed with Warnings"),
                        ("ocr_pending", "OCR Pending"), ("failed", "Failed"),
                    ],
                )),
                ("low_confidence_pages", models.JSONField(default=list, blank=True)),
                ("textract_job_id", models.CharField(max_length=255, blank=True, default="")),
                ("entity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="governing_documents",
                    to="core.entity",
                )),
                ("uploaded_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="uploaded_governing_docs",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("archived_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="archived_governing_docs",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-uploaded_at"],
            },
        ),
        migrations.AddIndex(
            model_name="governingdocument",
            index=models.Index(
                fields=["entity", "document_type", "status"],
                name="core_govdoc_entity_type_idx",
            ),
        ),

        # =====================================================================
        # NEW MODEL: LegalDocumentTemplate
        # =====================================================================
        migrations.CreateModel(
            name="LegalDocumentTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("document_type", models.CharField(
                    max_length=50, unique=True,
                    choices=[
                        ("div7a_loan_agreement", "Div 7A Loan Agreement"),
                        ("trust_deed_change_trustee", "Trust Deed — Change Trustee"),
                        ("trust_deed_add_beneficiary", "Trust Deed — Add Beneficiary"),
                        ("trust_deed_remove_beneficiary", "Trust Deed — Remove Beneficiary"),
                        ("trust_deed_extend_vesting", "Trust Deed — Extend Vesting"),
                        ("trust_deed_update_distribution", "Trust Deed — Update Distribution"),
                        ("company_constitution", "Company Constitution"),
                        ("company_constitution_special", "Company Constitution — Special Purpose"),
                        ("discretionary_trust_deed", "Discretionary Trust Deed"),
                        ("unit_trust_deed", "Unit Trust Deed"),
                        ("partnership_agreement", "Partnership Agreement"),
                        ("dividend_statement", "Dividend Statement"),
                        ("dividend_minutes", "Dividend Declaration Minutes"),
                        ("solvency_resolution", "Solvency Resolution"),
                        ("directors_declaration", "Director's Declaration"),
                        ("directors_declaration_large", "Director's Declaration — Large Proprietary"),
                        ("directors_declaration_gp", "Director's Declaration — General Purpose"),
                        ("directors_report", "Director's Report"),
                        ("shareholder_loan_ack", "Shareholder Loan Acknowledgment"),
                        ("partner_statement", "Partner Statement"),
                        ("partnership_tax_summary", "Partnership Tax Summary"),
                        ("engagement_letter", "Client Engagement Letter"),
                        ("management_rep_letter", "Management Representation Letter"),
                        ("management_rep_letter_trust", "Management Representation Letter — Trust"),
                        ("management_rep_letter_partnership", "Management Representation Letter — Partnership"),
                        ("client_cover_letter", "Client Cover Letter"),
                        ("distribution_minutes", "Trust Distribution Minutes"),
                        ("section_100a_summary", "Section 100A Summary"),
                    ],
                )),
                ("entity_types", models.JSONField(default=list, blank=True)),
                ("template_file", models.FileField(upload_to="legal_templates/")),
                ("version", models.PositiveIntegerField(default=1)),
                ("is_active", models.BooleanField(default=True)),
                ("solicitor_approved", models.BooleanField(default=False)),
                ("solicitor_name", models.CharField(max_length=255, blank=True, default="")),
                ("approval_date", models.DateField(null=True, blank=True)),
                ("variable_schema", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_legal_templates",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["name"],
            },
        ),

        # =====================================================================
        # NEW MODEL: LegalDocument
        # =====================================================================
        migrations.CreateModel(
            name="LegalDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("document_type", models.CharField(
                    max_length=50,
                    choices=[
                        ("div7a_loan_agreement", "Div 7A Loan Agreement"),
                        ("trust_deed_change_trustee", "Trust Deed — Change Trustee"),
                        ("trust_deed_add_beneficiary", "Trust Deed — Add Beneficiary"),
                        ("trust_deed_remove_beneficiary", "Trust Deed — Remove Beneficiary"),
                        ("trust_deed_extend_vesting", "Trust Deed — Extend Vesting"),
                        ("trust_deed_update_distribution", "Trust Deed — Update Distribution"),
                        ("company_constitution", "Company Constitution"),
                        ("company_constitution_special", "Company Constitution — Special Purpose"),
                        ("discretionary_trust_deed", "Discretionary Trust Deed"),
                        ("unit_trust_deed", "Unit Trust Deed"),
                        ("partnership_agreement", "Partnership Agreement"),
                        ("dividend_statement", "Dividend Statement"),
                        ("dividend_minutes", "Dividend Declaration Minutes"),
                        ("solvency_resolution", "Solvency Resolution"),
                        ("directors_declaration", "Director's Declaration"),
                        ("directors_declaration_large", "Director's Declaration — Large Proprietary"),
                        ("directors_declaration_gp", "Director's Declaration — General Purpose"),
                        ("directors_report", "Director's Report"),
                        ("shareholder_loan_ack", "Shareholder Loan Acknowledgment"),
                        ("partner_statement", "Partner Statement"),
                        ("partnership_tax_summary", "Partnership Tax Summary"),
                        ("engagement_letter", "Client Engagement Letter"),
                        ("management_rep_letter", "Management Representation Letter"),
                        ("management_rep_letter_trust", "Management Representation Letter — Trust"),
                        ("management_rep_letter_partnership", "Management Representation Letter — Partnership"),
                        ("client_cover_letter", "Client Cover Letter"),
                        ("distribution_minutes", "Trust Distribution Minutes"),
                        ("section_100a_summary", "Section 100A Summary"),
                    ],
                )),
                ("title", models.CharField(max_length=255, blank=True, default="", help_text="Human-readable document title")),
                ("version", models.PositiveIntegerField(default=1)),
                ("context_data", models.JSONField(default=dict, blank=True, help_text="Structured context data used for rendering")),
                ("status", models.CharField(
                    max_length=10, default="draft",
                    choices=[("draft", "Draft"), ("generated", "Generated"), ("final", "Final"), ("executed", "Executed")],
                )),
                ("parameters", models.JSONField(default=dict, blank=True)),
                ("generated_file", models.FileField(upload_to="legal_documents/", blank=True)),
                ("pdf_file", models.FileField(upload_to="legal_documents_pdf/", blank=True)),
                ("generated_at", models.DateTimeField(auto_now_add=True)),
                ("disclaimer_acknowledged", models.BooleanField(default=False)),
                ("disclaimer_acknowledged_at", models.DateTimeField(null=True, blank=True)),
                ("auto_saved_to_governing_docs", models.BooleanField(default=False)),
                ("fusesign_envelope_id", models.CharField(max_length=255, blank=True, default="")),
                ("fusesign_status", models.CharField(
                    max_length=15, default="not_sent",
                    choices=[
                        ("not_sent", "Not Sent"), ("sent", "Sent for Signing"),
                        ("signed", "Signed"), ("declined", "Declined"),
                    ],
                )),
                ("entity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="legal_documents",
                    to="core.entity",
                )),
                ("financial_year", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="legal_documents",
                    to="core.financialyear",
                )),
                ("template", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="generated_documents",
                    to="core.legaldocumenttemplate",
                )),
                ("generated_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="generated_legal_docs",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("governing_document", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="generated_legal_docs",
                    to="core.governingdocument",
                )),
            ],
            options={
                "ordering": ["-generated_at"],
            },
        ),
        migrations.AddIndex(
            model_name="legaldocument",
            index=models.Index(
                fields=["entity", "document_type"],
                name="core_legdoc_entity_type_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="legaldocument",
            index=models.Index(
                fields=["financial_year", "document_type"],
                name="core_legdoc_fy_type_idx",
            ),
        ),

        # =====================================================================
        # NEW MODEL: EvaClientSummary
        # =====================================================================
        migrations.CreateModel(
            name="EvaClientSummary",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("format_type", models.CharField(
                    max_length=15, default="bullet",
                    choices=[("bullet", "Bullet Point"), ("narrative", "Narrative")],
                )),
                ("financial_highlights", models.TextField(blank=True, default="")),
                ("compliance_status", models.TextField(blank=True, default="")),
                ("tax_position", models.TextField(blank=True, default="")),
                ("recommendations", models.TextField(blank=True, default="")),
                ("year_on_year_comparison", models.TextField(blank=True, default="")),
                ("full_content", models.TextField(blank=True, default="")),
                ("version", models.PositiveIntegerField(default=1)),
                ("model_used", models.CharField(max_length=10, blank=True, default="")),
                ("generated_at", models.DateTimeField(auto_now_add=True)),
                ("financial_year", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="client_summaries",
                    to="core.financialyear",
                )),
            ],
            options={
                "ordering": ["-generated_at"],
            },
        ),

        # =====================================================================
        # NEW MODEL: DividendEvent
        # =====================================================================
        migrations.CreateModel(
            name="DividendEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("dividend_type", models.CharField(
                    max_length=10,
                    choices=[("interim", "Interim"), ("final", "Final"), ("special", "Special")],
                )),
                ("total_amount", models.DecimalField(max_digits=15, decimal_places=2)),
                ("franking_percentage", models.DecimalField(max_digits=5, decimal_places=2, default=100.00)),
                ("company_tax_rate", models.DecimalField(max_digits=5, decimal_places=2, default=25.00)),
                ("record_date", models.DateField()),
                ("payment_date", models.DateField()),
                ("declaration_date", models.DateField()),
                ("solvency_confirmed", models.BooleanField(default=False)),
                ("franking_account_opening_balance", models.DecimalField(
                    max_digits=15, decimal_places=2, null=True, blank=True,
                )),
                ("franking_account_closing_balance", models.DecimalField(
                    max_digits=15, decimal_places=2, null=True, blank=True,
                )),
                ("resolution_type", models.CharField(max_length=50, blank=True, default="board_resolution")),
                ("meeting_location", models.CharField(max_length=255, blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("entity", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="dividend_events",
                    to="core.entity",
                )),
                ("financial_year", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="dividend_events",
                    to="core.financialyear",
                )),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_dividend_events",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-declaration_date"],
            },
        ),

        # =====================================================================
        # NEW MODEL: DividendShareholderAllocation
        # =====================================================================
        migrations.CreateModel(
            name="DividendShareholderAllocation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("shares_held", models.PositiveIntegerField(default=0)),
                ("dividend_amount", models.DecimalField(max_digits=15, decimal_places=2)),
                ("franking_credit", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("withholding_tax", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("dividend_event", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="allocations",
                    to="core.dividendevent",
                )),
                ("shareholder", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="dividend_allocations",
                    to="core.entityofficer",
                )),
                ("dividend_statement", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="dividend_allocations",
                    to="core.legaldocument",
                )),
            ],
            options={
                "unique_together": {("dividend_event", "shareholder")},
            },
        ),

        # =====================================================================
        # NEW MODEL: EngagementLetterConfig
        # =====================================================================
        migrations.CreateModel(
            name="EngagementLetterConfig",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("services_engaged", models.JSONField(default=list, blank=True)),
                ("fee_amount", models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)),
                ("fee_basis", models.CharField(
                    max_length=15, default="fixed",
                    choices=[("fixed", "Fixed Fee"), ("hourly", "Hourly Rate"), ("value_based", "Value-Based")],
                )),
                ("additional_terms", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("entity", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="engagement_letter_config",
                    to="core.entity",
                )),
                ("last_generated_fy", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="core.financialyear",
                )),
            ],
        ),
    ]
