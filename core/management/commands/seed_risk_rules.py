"""
Management command to seed the 65 ATO compliance risk rules and reference data.

Usage:
    python manage.py seed_risk_rules
    python manage.py seed_risk_rules --clear   # Clear existing rules first
"""

from django.core.management.base import BaseCommand
from core.models import RiskRule, RiskReferenceData


class Command(BaseCommand):
    help = "Seed the risk engine with 65 ATO compliance rules and reference data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true",
            help="Clear existing rules and reference data before seeding",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            RiskRule.objects.all().delete()
            RiskReferenceData.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared existing rules and reference data."))

        rules_created = 0
        ref_created = 0

        # --- SEED REFERENCE DATA ---
        for key, value, desc, fy in REFERENCE_DATA:
            _, created = RiskReferenceData.objects.update_or_create(
                key=key,
                defaults={"value": value, "description": desc, "applicable_fy": fy},
            )
            if created:
                ref_created += 1

        # --- SEED RISK RULES ---
        for rule_data in RISK_RULES:
            _, created = RiskRule.objects.update_or_create(
                rule_id=rule_data["rule_id"],
                defaults={
                    "category": rule_data["category"],
                    "title": rule_data["title"],
                    "description": rule_data["description"],
                    "severity": rule_data["severity"],
                    "tier": rule_data["tier"],
                    "applicable_entities": rule_data["applicable_entities"],
                    "trigger_config": rule_data["trigger_config"],
                    "recommended_action": rule_data["recommended_action"],
                    "legislation_ref": rule_data["legislation_ref"],
                    "is_active": True,
                },
            )
            if created:
                rules_created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {rules_created} new rules (total: {RiskRule.objects.count()}) "
            f"and {ref_created} new reference data items (total: {RiskReferenceData.objects.count()})."
        ))


# ============================================================================
# REFERENCE DATA
# ============================================================================
# (key, value, description, applicable_fy)

REFERENCE_DATA = [
    # Superannuation
    ("sg_rate", "11.5", "Superannuation Guarantee rate for FY2025", "FY2025"),
    ("sg_rate_fy2024", "11.0", "Superannuation Guarantee rate for FY2024", "FY2024"),
    ("sg_rate_fy2026", "12.0", "Superannuation Guarantee rate for FY2026", "FY2026"),
    ("sg_max_earnings_base", "65070", "Maximum super contribution base per quarter FY2025", "FY2025"),

    # Division 7A — Benchmark Interest Rates (ATO QC 17928)
    # Source: https://www.ato.gov.au/tax-rates-and-codes/division-7a-benchmark-interest-rate
    # Rates stored as percentages (e.g. 8.37 = 8.37%). The engine divides by 100.
    # New rates can be added here or via Django admin — the engine checks DB first.
    ("div7a_benchmark_rate_fy2026", "8.37", "Division 7A benchmark interest rate FY2026 — RBA rate published 6 Jun 2025", "FY2026"),
    ("div7a_benchmark_rate_fy2025", "8.77", "Division 7A benchmark interest rate FY2025 — RBA rate published 7 Jun 2024", "FY2025"),
    ("div7a_benchmark_rate_fy2024", "8.27", "Division 7A benchmark interest rate FY2024 — RBA rate published 7 Jun 2023", "FY2024"),
    ("div7a_benchmark_rate_fy2023", "4.77", "Division 7A benchmark interest rate FY2023 — RBA rate published 2 Jun 2022", "FY2023"),
    ("div7a_benchmark_rate_fy2022", "4.52", "Division 7A benchmark interest rate FY2022 — RBA rate published 2 Jun 2021", "FY2022"),
    ("div7a_benchmark_rate_fy2021", "4.52", "Division 7A benchmark interest rate FY2021 — RBA rate published 2 Jun 2020", "FY2021"),
    ("div7a_benchmark_rate_fy2020", "5.37", "Division 7A benchmark interest rate FY2020 — RBA rate published May 2019", "FY2020"),
    ("div7a_benchmark_rate_fy2019", "5.20", "Division 7A benchmark interest rate FY2019 — TD 2018/14", "FY2019"),
    ("div7a_benchmark_rate_fy2018", "5.30", "Division 7A benchmark interest rate FY2018 — TD 2017/17", "FY2018"),
    ("div7a_benchmark_rate_fy2017", "5.40", "Division 7A benchmark interest rate FY2017 — TD 2016/11", "FY2017"),
    ("div7a_benchmark_rate_fy2016", "5.45", "Division 7A benchmark interest rate FY2016 — TD 2015/15", "FY2016"),
    ("div7a_benchmark_rate_fy2015", "5.95", "Division 7A benchmark interest rate FY2015 — TD 2014/20", "FY2015"),
    ("div7a_benchmark_rate_fy2014", "6.20", "Division 7A benchmark interest rate FY2014 — TD 2013/17", "FY2014"),
    ("div7a_benchmark_rate_fy2013", "7.05", "Division 7A benchmark interest rate FY2013 — TD 2012/15", "FY2013"),
    ("div7a_benchmark_rate_fy2012", "7.80", "Division 7A benchmark interest rate FY2012 — TD 2011/20", "FY2012"),
    ("div7a_benchmark_rate_fy2011", "7.40", "Division 7A benchmark interest rate FY2011 — TD 2010/18", "FY2011"),
    ("div7a_benchmark_rate_fy2010", "5.75", "Division 7A benchmark interest rate FY2010 — TD 2009/16", "FY2010"),
    ("div7a_benchmark_rate_fy2009", "9.45", "Division 7A benchmark interest rate FY2009 — TD 2008/19", "FY2009"),
    ("div7a_benchmark_rate_fy2008", "8.05", "Division 7A benchmark interest rate FY2008 — TD 2007/23", "FY2008"),
    ("div7a_benchmark_rate_fy2007", "7.55", "Division 7A benchmark interest rate FY2007 — TD 2006/45", "FY2007"),
    ("div7a_benchmark_rate_fy2006", "7.30", "Division 7A benchmark interest rate FY2006 — TD 2005/31", "FY2006"),
    ("div7a_benchmark_rate_fy2005", "7.05", "Division 7A benchmark interest rate FY2005 — TD 2004/28", "FY2005"),
    ("div7a_benchmark_rate_fy2004", "6.55", "Division 7A benchmark interest rate FY2004 — TD 2003/19", "FY2004"),
    ("div7a_benchmark_rate_fy2003", "6.30", "Division 7A benchmark interest rate FY2003 — TD 2002/15", "FY2003"),
    ("div7a_benchmark_rate_fy2002", "6.80", "Division 7A benchmark interest rate FY2002 — TD 2001/20", "FY2002"),
    ("div7a_benchmark_rate_fy2001", "7.80", "Division 7A benchmark interest rate FY2001 — TD 2001/1", "FY2001"),
    ("div7a_benchmark_rate_fy2000", "6.50", "Division 7A benchmark interest rate FY2000 — TD 1999/39", "FY2000"),
    ("div7a_benchmark_rate_fy1999", "6.70", "Division 7A benchmark interest rate FY1999 — TD 98/21", "FY1999"),
    # Legacy alias for backward compat (points to FY2025)
    ("div7a_benchmark_rate", "8.77", "Division 7A benchmark interest rate (current default — FY2025)", "FY2025"),
    ("div7a_min_repayment_pct", "5.0", "Minimum annual repayment % for Div 7A 7-year loan", ""),
    ("div7a_loan_threshold", "0", "Minimum loan balance to trigger Div 7A check", ""),

    # GST
    ("gst_registration_threshold", "75000", "GST registration turnover threshold", ""),
    ("gst_benchmark_ratio", "11", "Expected GST-to-revenue ratio (approx 1/11 for 10% GST)", ""),

    # Variance thresholds
    ("variance_pct_threshold", "20", "Default percentage variance threshold for Tier 1", ""),
    ("variance_abs_threshold", "5000", "Default absolute dollar variance threshold for Tier 1", ""),
    ("revenue_variance_pct", "15", "Revenue variance percentage threshold", ""),
    ("expense_variance_pct", "20", "Expense variance percentage threshold", ""),

    # ATO benchmarks (general)
    ("ato_motor_vehicle_pct", "15", "ATO benchmark: motor vehicle expenses as % of revenue", ""),
    ("ato_travel_pct", "10", "ATO benchmark: travel expenses as % of revenue", ""),
    ("ato_entertainment_pct", "5", "ATO benchmark: entertainment expenses as % of revenue", ""),
    ("ato_contractor_pct", "40", "ATO benchmark: contractor payments as % of revenue", ""),
    ("ato_rent_pct", "20", "ATO benchmark: rent expenses as % of revenue", ""),

    # Solvency
    ("current_ratio_min", "1.0", "Minimum acceptable current ratio", ""),
    ("debt_equity_max", "3.0", "Maximum acceptable debt-to-equity ratio", ""),

    # SMSF
    ("smsf_concessional_cap", "30000", "Concessional contribution cap FY2025", "FY2025"),
    ("smsf_non_concessional_cap", "120000", "Non-concessional contribution cap FY2025", "FY2025"),
    ("smsf_transfer_balance_cap", "1900000", "Transfer balance cap FY2025", "FY2025"),

    # FBT
    ("fbt_rate", "47", "FBT rate (%)", ""),
    ("fbt_car_statutory_pct", "20", "FBT statutory fraction for car benefits", ""),

    # Tax rates
    ("company_tax_rate_base", "25", "Base rate entity company tax rate (%)", ""),
    ("company_tax_rate_full", "30", "Full company tax rate (%)", ""),
    ("base_rate_entity_threshold", "50000000", "Aggregated turnover threshold for base rate entity", ""),
]


# ============================================================================
# 65 RISK RULES
# ============================================================================

RISK_RULES = [
    # -----------------------------------------------------------------------
    # DIVISION 7A (Rules D7A-01 to D7A-06)
    # -----------------------------------------------------------------------
    {
        "rule_id": "D7A-01",
        "category": "division_7a",
        "title": "Director/shareholder loan — Div 7A risk",
        "description": (
            "Loan accounts totalling {total} detected across {count} account(s) for {entity_name}. "
            "Debit balances in director or shareholder loan accounts may constitute deemed dividends "
            "under Division 7A unless a compliant loan agreement is in place."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "loan_check",
            "account_keywords": ["director loan", "shareholder loan", "loan to director",
                                 "loan to shareholder", "related party loan", "loan - director"],
            "check_type": "div7a_loan",
        },
        "recommended_action": (
            "1. Confirm whether a compliant Division 7A loan agreement exists. "
            "2. Verify minimum yearly repayments have been made. "
            "3. Calculate benchmark interest at the ATO rate. "
            "4. If no agreement exists, the loan may be treated as an unfranked dividend."
        ),
        "legislation_ref": "ITAA 1936 Division 7A (s109D-109Q)",
    },
    {
        "rule_id": "D7A-02",
        "category": "division_7a",
        "title": "Div 7A — Minimum repayment not met",
        "description": (
            "Director/shareholder loan balance of {total} for {entity_name}. "
            "Verify that minimum annual repayments have been made per the loan agreement."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "loan_check",
            "account_keywords": ["director loan", "shareholder loan"],
            "check_type": "div7a_repayment",
        },
        "recommended_action": (
            "Calculate the minimum yearly repayment based on the loan agreement terms "
            "and the ATO benchmark interest rate. Verify payments were made before the lodgement date."
        ),
        "legislation_ref": "ITAA 1936 s109E",
    },
    {
        "rule_id": "D7A-03",
        "category": "division_7a",
        "title": "Company paying private expenses",
        "description": (
            "Accounts with keywords suggesting private expenses paid by the company detected "
            "for {entity_name}. Total: {total}. These may constitute Div 7A payments or FBT liabilities."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["private", "personal", "drawings", "owner expense"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review all private expense accounts. Determine if amounts should be treated as "
            "Div 7A loans, deemed dividends, or FBT-reportable benefits."
        ),
        "legislation_ref": "ITAA 1936 s109C, s109D",
    },
    {
        "rule_id": "D7A-04",
        "category": "division_7a",
        "title": "Intercompany loan — Div 7A interposed entity",
        "description": (
            "Intercompany loan accounts totalling {total} detected for {entity_name}. "
            "Loans between related entities may trigger Div 7A through interposed entity provisions."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "loan_check",
            "account_keywords": ["intercompany", "inter-company", "related entity loan",
                                 "loan to trust", "loan to partnership"],
            "check_type": "div7a_loan",
        },
        "recommended_action": (
            "Review interposed entity provisions. Determine if the ultimate beneficiary "
            "is a shareholder or associate. Consider whether a compliant loan agreement is needed."
        ),
        "legislation_ref": "ITAA 1936 s109T-109V (interposed entities)",
    },
    {
        "rule_id": "D7A-05",
        "category": "division_7a",
        "title": "Unpaid present entitlement — trust to company",
        "description": (
            "Trust distribution receivable or unpaid present entitlement detected for {entity_name}. "
            "Unpaid present entitlements from a trust to a company may trigger Div 7A."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company", "trust"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["unpaid present entitlement", "upe", "distribution receivable",
                                 "trust distribution owing"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review the UPE balance. From 1 July 2022, new UPEs from a trust to a company "
            "are treated as Div 7A loans. Existing UPEs may be subject to transitional rules."
        ),
        "legislation_ref": "ITAA 1936 s109XA-109XB, TD 2022/11",
    },
    {
        "rule_id": "D7A-06",
        "category": "division_7a",
        "title": "Div 7A benchmark interest not charged",
        "description": (
            "Director/shareholder loans of {total} detected for {entity_name}. "
            "Verify that benchmark interest has been charged at the ATO rate."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "loan_check",
            "account_keywords": ["director loan", "shareholder loan", "loan to director"],
            "check_type": "div7a_loan",
        },
        "recommended_action": (
            "Check that interest has been charged at the ATO benchmark rate and "
            "included in the company's assessable income."
        ),
        "legislation_ref": "ITAA 1936 s109F, s109N",
    },

    # -----------------------------------------------------------------------
    # SUPERANNUATION (Rules SG-01 to SG-05)
    # -----------------------------------------------------------------------
    {
        "rule_id": "SG-01",
        "category": "superannuation",
        "title": "Superannuation guarantee shortfall",
        "description": (
            "Total wages/salaries of {wages} with super expense of {super_total} for {entity_name}. "
            "Expected super at {sg_rate} is {expected}. Potential shortfall of {shortfall}."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "superannuation",
            "wages_keywords": ["wages", "salary", "salaries", "gross pay", "staff costs"],
            "super_keywords": ["superannuation", "super guarantee", "super expense", "sgc"],
        },
        "recommended_action": (
            "1. Reconcile super payments to wages records. "
            "2. Verify all eligible employees received the correct SG rate. "
            "3. Check payment dates — SG must be paid within 28 days of quarter end. "
            "4. Lodge a Superannuation Guarantee Charge statement if shortfall confirmed."
        ),
        "legislation_ref": "Superannuation Guarantee (Administration) Act 1992",
    },
    {
        "rule_id": "SG-02",
        "category": "superannuation",
        "title": "Contractor payments — SG obligation check",
        "description": (
            "Contractor/subcontractor payments detected for {entity_name}. "
            "Review whether contractors are 'employees' for SG purposes under the extended definition."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["contractor", "subcontractor", "sub-contractor", "labour hire"],
            "threshold_value": 10000,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review contractor arrangements against the 'wholly or principally for labour' test. "
            "Contractors paid principally for labour may be deemed employees for SG purposes."
        ),
        "legislation_ref": "SGA Act 1992 s12(3) — extended definition of employee",
    },
    {
        "rule_id": "SG-03",
        "category": "superannuation",
        "title": "Director fees without super",
        "description": (
            "Director fee payments detected for {entity_name} but no corresponding "
            "superannuation expense identified. Directors are entitled to SG from 1 July 2019."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["director fee", "director remuneration", "directors fees"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Verify that SG has been paid on director fees. Since 1 July 2019, "
            "directors are no longer exempt from SG regardless of whether they are also employees."
        ),
        "legislation_ref": "SGA Act 1992 (removal of $450/month threshold from 1 July 2022)",
    },
    {
        "rule_id": "SG-04",
        "category": "superannuation",
        "title": "Super paid to wrong fund or late",
        "description": (
            "Superannuation expense of {total} for {entity_name}. "
            "Verify payments were made to compliant funds within statutory deadlines."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["superannuation", "super guarantee", "super expense"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Confirm all SG payments were made within 28 days of the end of each quarter "
            "to a compliant superannuation fund. Late payments attract the SG Charge."
        ),
        "legislation_ref": "SGA Act 1992 s23, s46",
    },
    {
        "rule_id": "SG-05",
        "category": "superannuation",
        "title": "Concessional contribution cap exceeded (SMSF)",
        "description": (
            "SMSF contributions detected for {entity_name}. "
            "Verify that concessional contributions do not exceed the annual cap."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["employer contribution", "concessional contribution",
                                 "member contribution - deductible", "salary sacrifice"],
            "threshold_key": "smsf_concessional_cap",
            "comparison": "gt",
        },
        "recommended_action": (
            "Review total concessional contributions per member against the annual cap. "
            "Excess contributions are taxed at the member's marginal rate plus an interest charge."
        ),
        "legislation_ref": "ITAA 1997 s291-20, s291-25",
    },

    # -----------------------------------------------------------------------
    # GST (Rules GST-01 to GST-06)
    # -----------------------------------------------------------------------
    {
        "rule_id": "GST-01",
        "category": "gst",
        "title": "GST claimed exceeds benchmark ratio",
        "description": (
            "GST input credits of {gst_total} represent {ratio} of revenue ({revenue}) for {entity_name}. "
            "This exceeds the benchmark of {benchmark}. Review for overclaimed input credits."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "gst_check",
            "check_type": "gst_ratio",
        },
        "recommended_action": (
            "Review GST input credits for accuracy. Check for private expenses incorrectly "
            "claimed, capital items that should be separately reported, and input-taxed supplies."
        ),
        "legislation_ref": "A New Tax System (Goods and Services Tax) Act 1999 Div 11",
    },
    {
        "rule_id": "GST-02",
        "category": "gst",
        "title": "Unclassified bank transactions pending",
        "description": (
            "{count} unclassified bank transactions remain for {entity_name}. "
            "These must be reviewed and coded before GST reporting can be completed."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "gst_check",
            "check_type": "gst_unclassified",
        },
        "recommended_action": (
            "Review and classify all pending bank transactions. "
            "Ensure correct GST treatment is applied to each transaction."
        ),
        "legislation_ref": "GST Act 1999 s29-10",
    },
    {
        "rule_id": "GST-03",
        "category": "gst",
        "title": "GST on capital purchases not separately reported",
        "description": (
            "Capital asset purchases detected for {entity_name} totalling {total}. "
            "GST on capital purchases over $10,000 must be reported separately on the BAS."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["plant", "equipment", "motor vehicle", "furniture",
                                 "computer", "capital purchase", "fixed asset"],
            "threshold_value": 10000,
            "comparison": "gt",
        },
        "recommended_action": (
            "Ensure GST on capital purchases exceeding $10,000 is reported at label G10 "
            "on the BAS, not at G11."
        ),
        "legislation_ref": "GST Act 1999 s129-40",
    },
    {
        "rule_id": "GST-04",
        "category": "gst",
        "title": "Revenue below GST registration threshold",
        "description": (
            "Total revenue of {total} for {entity_name} is below the GST registration threshold "
            "of {threshold}. Consider whether GST registration is still required."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": [],
            "threshold_key": "gst_registration_threshold",
            "comparison": "lt",
        },
        "recommended_action": (
            "If the entity's projected turnover is below $75,000, GST registration is optional. "
            "Consider deregistering if the entity is not making taxable supplies."
        ),
        "legislation_ref": "GST Act 1999 s23-15",
    },
    {
        "rule_id": "GST-05",
        "category": "gst",
        "title": "Input-taxed supplies detected — apportionment required",
        "description": (
            "Interest income or residential rent detected for {entity_name}. "
            "Entities making input-taxed supplies may need to apportion GST input credits."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["interest income", "interest received", "residential rent",
                                 "rental income - residential"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "If the entity makes both taxable and input-taxed supplies, GST input credits "
            "must be apportioned. Apply the 'fair and reasonable' method or the turnover method."
        ),
        "legislation_ref": "GST Act 1999 Div 129",
    },
    {
        "rule_id": "GST-06",
        "category": "gst",
        "title": "GST liability balance carried forward",
        "description": (
            "GST liability/clearing account has a balance of {total} for {entity_name}. "
            "Verify this reconciles to the latest BAS lodgement."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["gst collected", "gst payable", "gst clearing", "gst liability",
                                 "bas liability"],
            "threshold_value": 0,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "Reconcile the GST clearing account to the latest BAS lodgement. "
            "Any balance should represent the current quarter's GST obligation."
        ),
        "legislation_ref": "GST Act 1999 s33-3",
    },

    # -----------------------------------------------------------------------
    # SOLVENCY (Rules SOL-01 to SOL-04)
    # -----------------------------------------------------------------------
    {
        "rule_id": "SOL-01",
        "category": "solvency",
        "title": "Current ratio below 1.0 — solvency concern",
        "description": (
            "Current ratio of {ratio} for {entity_name} (current assets {current_assets}, "
            "current liabilities {current_liabilities}). The entity may not be able to meet "
            "short-term obligations as they fall due."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "solvency",
            "check_type": "current_ratio",
            "threshold_value": "1.0",
        },
        "recommended_action": (
            "1. Assess whether the entity can pay its debts as and when they fall due. "
            "2. Consider directors' duties under s588G of the Corporations Act. "
            "3. Document the solvency assessment in the working papers. "
            "4. Consider whether a going concern note is required."
        ),
        "legislation_ref": "Corporations Act 2001 s588G (duty to prevent insolvent trading)",
    },
    {
        "rule_id": "SOL-02",
        "category": "solvency",
        "title": "Net asset deficiency",
        "description": (
            "Net assets of {net_assets} for {entity_name} (total assets {total_assets}, "
            "total liabilities {total_liabilities}). The entity has a net liability position."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "solvency",
            "check_type": "net_assets",
        },
        "recommended_action": (
            "1. Assess going concern status. "
            "2. Consider whether a going concern note is required in the financial statements. "
            "3. Discuss with directors/trustees the entity's ability to continue operations. "
            "4. Document the assessment thoroughly."
        ),
        "legislation_ref": "AASB 101 para 25-26 (going concern assessment)",
    },
    {
        "rule_id": "SOL-03",
        "category": "solvency",
        "title": "Accumulated losses exceed paid-up capital",
        "description": (
            "Accumulated losses detected for {entity_name}. "
            "Retained earnings/accumulated losses may exceed the entity's paid-up capital."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["retained earnings", "accumulated losses", "retained losses",
                                 "accumulated deficit"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review whether accumulated losses exceed paid-up capital. "
            "Consider the impact on dividend franking and the entity's solvency position."
        ),
        "legislation_ref": "Corporations Act 2001 s254T (dividends), AASB 101",
    },
    {
        "rule_id": "SOL-04",
        "category": "solvency",
        "title": "Overdue ATO liabilities",
        "description": (
            "ATO liability accounts with balance of {total} detected for {entity_name}. "
            "Verify whether any amounts are overdue."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["ato", "tax payable", "income tax", "payg instalment",
                                 "activity statement"],
            "threshold_value": 0,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "Reconcile ATO liability accounts to the ATO integrated client account. "
            "Identify any overdue amounts and arrange payment plans if necessary."
        ),
        "legislation_ref": "TAA 1953 Sch 1",
    },

    # -----------------------------------------------------------------------
    # EXPENSES (Rules EXP-01 to EXP-10)
    # -----------------------------------------------------------------------
    {
        "rule_id": "EXP-01",
        "category": "expenses",
        "title": "Motor vehicle expenses exceed ATO benchmark",
        "description": (
            "Motor vehicle expenses of {expense_total} represent {ratio} of revenue ({revenue}) "
            "for {entity_name}. ATO benchmark is {benchmark}."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "expense_benchmark",
            "expense_keywords": ["motor vehicle", "car expense", "fuel", "vehicle",
                                 "registration", "car lease"],
            "benchmark_key": "ato_motor_vehicle_pct",
        },
        "recommended_action": (
            "Review motor vehicle expense claims. Ensure logbooks are maintained, "
            "private use is excluded, and FBT obligations are considered."
        ),
        "legislation_ref": "ITAA 1997 s28-12 to s28-185 (car expenses)",
    },
    {
        "rule_id": "EXP-02",
        "category": "expenses",
        "title": "Travel expenses exceed ATO benchmark",
        "description": (
            "Travel expenses of {expense_total} represent {ratio} of revenue ({revenue}) "
            "for {entity_name}. ATO benchmark is {benchmark}."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "expense_benchmark",
            "expense_keywords": ["travel", "accommodation", "airfare", "flight"],
            "benchmark_key": "ato_travel_pct",
        },
        "recommended_action": (
            "Review travel expense claims for business purpose substantiation. "
            "Ensure private travel components are excluded."
        ),
        "legislation_ref": "ITAA 1997 s8-1 (general deductions), TR 2021/1",
    },
    {
        "rule_id": "EXP-03",
        "category": "expenses",
        "title": "Entertainment expenses — deductibility review",
        "description": (
            "Entertainment expenses of {expense_total} represent {ratio} of revenue ({revenue}) "
            "for {entity_name}. ATO benchmark is {benchmark}."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "expense_benchmark",
            "expense_keywords": ["entertainment", "meals", "dining", "hospitality"],
            "benchmark_key": "ato_entertainment_pct",
        },
        "recommended_action": (
            "Review entertainment expenses. Most entertainment is non-deductible (s32-5) "
            "unless it constitutes a fringe benefit. Consider FBT implications."
        ),
        "legislation_ref": "ITAA 1997 s32-5 (entertainment), FBTAA 1986",
    },
    {
        "rule_id": "EXP-04",
        "category": "expenses",
        "title": "Contractor payments exceed ATO benchmark",
        "description": (
            "Contractor payments of {expense_total} represent {ratio} of revenue ({revenue}) "
            "for {entity_name}. ATO benchmark is {benchmark}."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "expense_benchmark",
            "expense_keywords": ["contractor", "subcontractor", "sub-contractor", "labour hire"],
            "benchmark_key": "ato_contractor_pct",
        },
        "recommended_action": (
            "Review contractor arrangements. Verify TPAR reporting obligations. "
            "Assess whether contractors should be classified as employees for SG and PAYG purposes."
        ),
        "legislation_ref": "TAA 1953 Sch 1 s396-55 (TPAR), SGA Act 1992 s12",
    },
    {
        "rule_id": "EXP-05",
        "category": "expenses",
        "title": "Rent expenses exceed ATO benchmark",
        "description": (
            "Rent expenses of {expense_total} represent {ratio} of revenue ({revenue}) "
            "for {entity_name}. ATO benchmark is {benchmark}."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "expense_benchmark",
            "expense_keywords": ["rent", "lease", "premises"],
            "benchmark_key": "ato_rent_pct",
        },
        "recommended_action": (
            "Review rent expenses. If premises are leased from a related party, "
            "ensure the rent is at arm's length. Check for any personal use component."
        ),
        "legislation_ref": "ITAA 1997 s8-1",
    },
    {
        "rule_id": "EXP-06",
        "category": "expenses",
        "title": "Depreciation — immediate write-off review",
        "description": (
            "Depreciation expense detected for {entity_name} totalling {total}. "
            "Review eligibility for instant asset write-off provisions."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["depreciation", "amortisation", "amortization"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review depreciation schedules. Verify eligibility for the instant asset write-off "
            "($20,000 threshold for small business entities from 1 July 2024). "
            "Ensure assets are correctly classified and rates are appropriate."
        ),
        "legislation_ref": "ITAA 1997 Div 40, s328-180 (instant asset write-off)",
    },
    {
        "rule_id": "EXP-07",
        "category": "expenses",
        "title": "Bad debts written off — substantiation required",
        "description": (
            "Bad debt expense of {total} detected for {entity_name}. "
            "Verify that bad debts have been properly written off before year end."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["bad debt", "doubtful debt", "provision for doubtful"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Ensure bad debts were formally written off before 30 June. "
            "The debt must have been previously included in assessable income. "
            "Maintain documentation of recovery efforts."
        ),
        "legislation_ref": "ITAA 1997 s25-35 (bad debts)",
    },
    {
        "rule_id": "EXP-08",
        "category": "expenses",
        "title": "Repairs vs capital improvements",
        "description": (
            "Repairs and maintenance expense of {total} detected for {entity_name}. "
            "Large repair amounts may include capital improvements that should be depreciated."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["repairs", "maintenance", "repair and maintenance"],
            "threshold_value": 20000,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review large repair items. Distinguish between deductible repairs (restoring to "
            "original condition) and capital improvements (enhancing the asset). "
            "Capital items must be depreciated."
        ),
        "legislation_ref": "ITAA 1997 s25-10 (repairs), TR 97/23",
    },
    {
        "rule_id": "EXP-09",
        "category": "expenses",
        "title": "Legal fees — capital vs revenue",
        "description": (
            "Legal fees of {total} detected for {entity_name}. "
            "Review whether legal costs are revenue (deductible) or capital (non-deductible)."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["legal", "solicitor", "lawyer", "legal fees"],
            "threshold_value": 5000,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review legal fees. Costs related to the structure or acquisition of assets "
            "are capital in nature. Costs related to ongoing business operations are deductible."
        ),
        "legislation_ref": "ITAA 1997 s8-1, s25-5 (tax-related expenses)",
    },
    {
        "rule_id": "EXP-10",
        "category": "expenses",
        "title": "Donations — DGR status required",
        "description": (
            "Donation/gift expense of {total} detected for {entity_name}. "
            "Verify recipients hold Deductible Gift Recipient (DGR) status."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["donation", "gift", "charitable", "sponsorship"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Verify that all donation recipients hold DGR status. "
            "Non-DGR donations are not deductible. Sponsorship may be deductible as advertising."
        ),
        "legislation_ref": "ITAA 1997 Div 30 (gifts and contributions)",
    },

    # -----------------------------------------------------------------------
    # CAPITAL GAINS TAX (Rules CGT-01 to CGT-05)
    # -----------------------------------------------------------------------
    {
        "rule_id": "CGT-01",
        "category": "cgt",
        "title": "Capital gain detected — CGT event review",
        "description": (
            "Capital gain or asset disposal account detected for {entity_name} totalling {total}. "
            "Review CGT event classification and available concessions."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["capital gain", "profit on sale", "gain on disposal",
                                 "asset disposal", "profit on disposal"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "1. Identify the CGT event type. "
            "2. Calculate the capital gain using the appropriate method (indexation or discount). "
            "3. Review eligibility for small business CGT concessions (Div 152). "
            "4. For trusts, consider streaming of capital gains to beneficiaries."
        ),
        "legislation_ref": "ITAA 1997 Part 3-1 (CGT), Div 152 (small business concessions)",
    },
    {
        "rule_id": "CGT-02",
        "category": "cgt",
        "title": "Capital loss carried forward",
        "description": (
            "Capital loss account detected for {entity_name} totalling {total}. "
            "Verify correct treatment and carry-forward."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["capital loss", "loss on sale", "loss on disposal"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Capital losses can only be offset against capital gains, not ordinary income. "
            "Ensure losses are carried forward correctly and the continuity of ownership test is met."
        ),
        "legislation_ref": "ITAA 1997 s102-10 (capital losses)",
    },
    {
        "rule_id": "CGT-03",
        "category": "cgt",
        "title": "Property disposal — CGT and GST interaction",
        "description": (
            "Property-related disposal or sale detected for {entity_name}. "
            "Review CGT event, GST margin scheme eligibility, and withholding obligations."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["property sale", "land sale", "real estate", "property disposal"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "1. Calculate CGT on the property disposal. "
            "2. Consider GST margin scheme if applicable. "
            "3. Check foreign resident capital gains withholding obligations (s14-200). "
            "4. Verify cost base includes all eligible costs."
        ),
        "legislation_ref": "ITAA 1997 s104-10 (CGT event A1), GST Act s75-10 (margin scheme)",
    },
    {
        "rule_id": "CGT-04",
        "category": "cgt",
        "title": "Small business CGT concession eligibility",
        "description": (
            "Capital gain detected for {entity_name}. Review eligibility for small business "
            "CGT concessions (net asset value test, active asset test)."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["capital gain", "profit on sale", "gain on disposal"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review eligibility for Div 152 concessions: "
            "1. 15-year exemption (s152-105). "
            "2. 50% active asset reduction (s152-205). "
            "3. Retirement exemption (s152-305). "
            "4. Rollover (s152-410). "
            "Requires net asset value ≤ $6M or aggregated turnover < $2M."
        ),
        "legislation_ref": "ITAA 1997 Div 152",
    },
    {
        "rule_id": "CGT-05",
        "category": "cgt",
        "title": "Crypto/digital asset disposal",
        "description": (
            "Cryptocurrency or digital asset transactions detected for {entity_name} totalling {total}. "
            "Each disposal is a CGT event."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["crypto", "bitcoin", "ethereum", "digital asset", "cryptocurrency"],
            "threshold_value": 0,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "Each crypto disposal is a CGT event. Calculate gains/losses for each disposal. "
            "The 12-month discount may apply for assets held > 12 months. "
            "Crypto-to-crypto swaps are also CGT events."
        ),
        "legislation_ref": "ITAA 1997 s104-10, ATO guidance on cryptocurrency",
    },

    # -----------------------------------------------------------------------
    # TRUST (Rules TRU-01 to TRU-06)
    # -----------------------------------------------------------------------
    {
        "rule_id": "TRU-01",
        "category": "trust",
        "title": "Trust income not fully distributed",
        "description": (
            "Trust net income of {net_income} for {entity_name} but no distribution "
            "resolution recorded. Undistributed income may be taxed at the top marginal rate."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["trust"],
        "trigger_config": {
            "type": "trust_distribution",
            "check_type": "undistributed",
        },
        "recommended_action": (
            "1. Prepare a distribution resolution before 30 June. "
            "2. Ensure the resolution is consistent with the trust deed. "
            "3. Consider streaming of capital gains and franked dividends. "
            "4. If no resolution is made, the trustee is assessed under s99A at 47%."
        ),
        "legislation_ref": "ITAA 1936 s97, s99, s99A",
    },
    {
        "rule_id": "TRU-02",
        "category": "trust",
        "title": "Section 100A — reimbursement agreement risk",
        "description": (
            "Trust distributions for {entity_name} should be reviewed for Section 100A risk. "
            "Distributions to low-income beneficiaries where funds are redirected to others "
            "may be challenged by the ATO."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["trust"],
        "trigger_config": {
            "type": "trust_distribution",
            "check_type": "undistributed",
        },
        "recommended_action": (
            "Review distribution patterns for Section 100A risk. The ATO's updated guidance "
            "(TR 2022/4) targets arrangements where: "
            "1. Income is distributed to a low-tax beneficiary. "
            "2. The beneficiary does not retain the economic benefit. "
            "3. There is a reimbursement agreement (formal or informal)."
        ),
        "legislation_ref": "ITAA 1936 s100A, TR 2022/4",
    },
    {
        "rule_id": "TRU-03",
        "category": "trust",
        "title": "Trust deed review — distribution powers",
        "description": (
            "Verify that the trust deed for {entity_name} grants the trustee adequate "
            "powers to make the intended distributions, including streaming of capital gains."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["trust"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["capital gain", "franked dividend", "foreign income"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review the trust deed to confirm streaming powers exist. "
            "Without specific streaming powers, capital gains and franked dividends "
            "may need to be distributed proportionally."
        ),
        "legislation_ref": "ITAA 1997 Subdiv 115-C (streaming of capital gains)",
    },
    {
        "rule_id": "TRU-04",
        "category": "trust",
        "title": "Trust loss carry-forward — trust loss provisions",
        "description": (
            "Trust has a tax loss for {entity_name}. Trust losses are subject to the "
            "trust loss provisions and may not be deductible in future years."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["trust"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["tax loss", "carried forward loss", "prior year loss"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review trust loss provisions (Sch 2F ITAA 1936). "
            "Family trusts may need to make a family trust election (FTE) to carry forward losses. "
            "Non-fixed trusts face additional tests."
        ),
        "legislation_ref": "ITAA 1936 Sch 2F (trust losses)",
    },
    {
        "rule_id": "TRU-05",
        "category": "trust",
        "title": "Trustee remuneration — trust deed authority",
        "description": (
            "Trustee remuneration or management fees of {total} detected for {entity_name}. "
            "Verify the trust deed authorises trustee remuneration."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["trust"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["trustee fee", "trustee remuneration", "management fee",
                                 "trustee commission"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Verify the trust deed authorises the payment of trustee remuneration. "
            "If not authorised, the payment may not be deductible to the trust."
        ),
        "legislation_ref": "ITAA 1997 s8-1, trust deed provisions",
    },
    {
        "rule_id": "TRU-06",
        "category": "trust",
        "title": "SMSF — in-house asset rule",
        "description": (
            "Related party transactions or in-house assets detected for {entity_name}. "
            "SMSF in-house assets must not exceed 5% of total fund assets."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["related party", "in-house asset", "loan to member",
                                 "member loan", "related party investment"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review in-house asset levels. SMSF in-house assets (loans to members, "
            "investments in related parties, leases to related parties) must not exceed "
            "5% of total fund assets at market value."
        ),
        "legislation_ref": "SIS Act 1993 s71, s82-85 (in-house asset rules)",
    },

    # -----------------------------------------------------------------------
    # RELATED PARTY (Rules RP-01 to RP-05)
    # -----------------------------------------------------------------------
    {
        "rule_id": "RP-01",
        "category": "related_party",
        "title": "Related party transactions detected",
        "description": (
            "Related party transaction accounts totalling {total} detected for {entity_name}. "
            "Verify arm's length pricing and disclosure requirements."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["related party", "director", "shareholder", "associated entity",
                                 "intercompany"],
            "threshold_value": 0,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "1. Identify all related party transactions. "
            "2. Verify arm's length pricing (Part IVA, transfer pricing rules). "
            "3. Ensure proper disclosure in financial statements (AASB 124). "
            "4. Consider Div 7A implications for company-related party transactions."
        ),
        "legislation_ref": "AASB 124 (Related Party Disclosures), ITAA 1997 Part IVA",
    },
    {
        "rule_id": "RP-02",
        "category": "related_party",
        "title": "Management fees to related entities",
        "description": (
            "Management fees paid to related entities totalling {total} for {entity_name}. "
            "Verify commercial justification and arm's length pricing."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["management fee", "consulting fee - related", "service fee",
                                 "admin fee - related"],
            "threshold_value": 5000,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review management fee arrangements. Ensure there is a written agreement, "
            "the fee is commercially justified, and the recipient declares the income."
        ),
        "legislation_ref": "ITAA 1997 s8-1, Part IVA",
    },
    {
        "rule_id": "RP-03",
        "category": "related_party",
        "title": "Rent paid to related parties",
        "description": (
            "Rent paid to related parties totalling {total} for {entity_name}. "
            "Verify the rent is at market rates."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["rent - related", "lease - related", "premises - related",
                                 "rent to director", "rent to trust"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Obtain evidence that the rent is at market rates (e.g., independent valuation "
            "or comparable market rents). Above-market rent to related parties may be challenged."
        ),
        "legislation_ref": "ITAA 1997 s8-1, Part IVA",
    },
    {
        "rule_id": "RP-04",
        "category": "related_party",
        "title": "SMSF — related party acquisition",
        "description": (
            "Asset acquisitions from related parties detected for {entity_name}. "
            "SMSFs are generally prohibited from acquiring assets from related parties."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["related party purchase", "acquisition from member",
                                 "asset from related", "purchase from director"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review the acquisition against SIS Act s66. SMSFs can only acquire assets "
            "from related parties in limited circumstances (listed securities, business real property, "
            "certain in-house assets under 5%)."
        ),
        "legislation_ref": "SIS Act 1993 s66 (acquisition of assets from related parties)",
    },
    {
        "rule_id": "RP-05",
        "category": "related_party",
        "title": "Loans between related entities",
        "description": (
            "Loan balances between related entities totalling {total} for {entity_name}. "
            "Review for Div 7A, transfer pricing, and arm's length interest."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "loan_check",
            "account_keywords": ["loan to related", "loan from related", "intercompany loan",
                                 "inter-entity loan", "loan to trust", "loan from trust"],
            "check_type": "div7a_loan",
        },
        "recommended_action": (
            "1. Determine if Div 7A applies (company as lender). "
            "2. Verify arm's length interest is charged. "
            "3. Check for compliant loan agreements. "
            "4. Consider transfer pricing rules for cross-border arrangements."
        ),
        "legislation_ref": "ITAA 1936 Div 7A, ITAA 1997 Div 815 (transfer pricing)",
    },

    # -----------------------------------------------------------------------
    # FBT (Rules FBT-01 to FBT-04)
    # -----------------------------------------------------------------------
    {
        "rule_id": "FBT-01",
        "category": "fbt",
        "title": "Fringe benefits detected — FBT return required",
        "description": (
            "Fringe benefit expense accounts totalling {total} detected for {entity_name}. "
            "An FBT return may be required."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["fringe benefit", "fbt", "novated lease", "employee benefit"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review fringe benefits provided during the FBT year (1 April - 31 March). "
            "Determine if an FBT return is required and calculate the FBT liability."
        ),
        "legislation_ref": "FBTAA 1986",
    },
    {
        "rule_id": "FBT-02",
        "category": "fbt",
        "title": "Motor vehicle — FBT car benefit",
        "description": (
            "Motor vehicle expenses or car lease payments detected for {entity_name}. "
            "If vehicles are available for private use by employees, a car fringe benefit arises."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["motor vehicle", "car lease", "vehicle lease", "novated"],
            "threshold_value": 5000,
            "comparison": "gt",
        },
        "recommended_action": (
            "Determine if any vehicles are available for private use by employees or associates. "
            "Calculate the car fringe benefit using either the statutory formula or operating cost method. "
            "Maintain logbooks if using the operating cost method."
        ),
        "legislation_ref": "FBTAA 1986 s7-11 (car fringe benefits)",
    },
    {
        "rule_id": "FBT-03",
        "category": "fbt",
        "title": "Employee loans — FBT loan benefit",
        "description": (
            "Loans to employees detected for {entity_name} totalling {total}. "
            "Loans to employees at less than the benchmark rate create a loan fringe benefit."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["loan to employee", "staff loan", "employee advance"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Review employee loan terms. If interest is charged below the FBT benchmark rate, "
            "a loan fringe benefit arises on the difference."
        ),
        "legislation_ref": "FBTAA 1986 s16-19 (loan fringe benefits)",
    },
    {
        "rule_id": "FBT-04",
        "category": "fbt",
        "title": "Entertainment — FBT meal entertainment",
        "description": (
            "Meal entertainment or recreation expenses detected for {entity_name}. "
            "These may give rise to FBT obligations."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["entertainment", "meals", "staff amenities", "christmas party",
                                 "team building"],
            "threshold_value": 2000,
            "comparison": "gt",
        },
        "recommended_action": (
            "Determine if entertainment expenses give rise to FBT. "
            "Consider the minor benefit exemption ($300 per benefit) and the "
            "50/50 split method for meal entertainment."
        ),
        "legislation_ref": "FBTAA 1986 s37AA-37AD (meal entertainment)",
    },

    # -----------------------------------------------------------------------
    # GENERAL (Rules GEN-01 to GEN-09)
    # -----------------------------------------------------------------------
    {
        "rule_id": "GEN-01",
        "category": "general",
        "title": "Suspense account has balance",
        "description": (
            "Suspense account balance of {total} detected for {entity_name}. "
            "All suspense items must be cleared before finalisation."
        ),
        "severity": "MEDIUM",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["suspense", "clearing", "unallocated", "unclassified"],
            "threshold_value": 0,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "Investigate and clear all suspense account items. "
            "Allocate to the correct accounts before finalising the financial statements."
        ),
        "legislation_ref": "AASB 101 (presentation of financial statements)",
    },
    {
        "rule_id": "GEN-02",
        "category": "general",
        "title": "Unmapped accounts in trial balance",
        "description": (
            "{count} account(s) in the trial balance for {entity_name} are not mapped "
            "to the standard chart of accounts. These will not appear in the financial statements."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "balance_sign",
            "account_keywords": [],
            "expected_sign": "credit",
        },
        "recommended_action": (
            "Map all unmapped accounts to the appropriate financial statement line items. "
            "Unmapped accounts will be excluded from generated financial statements."
        ),
        "legislation_ref": "AASB 101",
    },
    {
        "rule_id": "GEN-03",
        "category": "general",
        "title": "Revenue accounts with debit balances",
        "description": (
            "{count} revenue account(s) have unexpected debit balances for {entity_name}. "
            "Revenue accounts should normally have credit balances."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "balance_sign",
            "account_keywords": ["revenue", "income", "sales", "fee income", "commission income"],
            "expected_sign": "credit",
        },
        "recommended_action": (
            "Investigate revenue accounts with debit balances. This may indicate "
            "refunds, reversals, or mispostings that need correction."
        ),
        "legislation_ref": "AASB 101",
    },
    {
        "rule_id": "GEN-04",
        "category": "general",
        "title": "Asset accounts with credit balances",
        "description": (
            "{count} asset account(s) have unexpected credit balances for {entity_name}. "
            "Asset accounts should normally have debit balances."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "balance_sign",
            "account_keywords": ["bank", "cash", "receivable", "inventory", "prepaid",
                                 "plant", "equipment", "investment"],
            "expected_sign": "debit",
        },
        "recommended_action": (
            "Investigate asset accounts with credit balances. This may indicate "
            "bank overdrafts (reclassify to liabilities), overpayments, or posting errors."
        ),
        "legislation_ref": "AASB 101 para 32-35 (offsetting)",
    },
    {
        "rule_id": "GEN-05",
        "category": "general",
        "title": "Trial balance does not balance",
        "description": (
            "The trial balance for {entity_name} has a net imbalance. "
            "Total debits do not equal total credits."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": [],
            "threshold_value": 1,
            "comparison": "gt",
        },
        "recommended_action": (
            "The trial balance must balance before any financial statements can be generated. "
            "Review recent journal entries and data imports for errors."
        ),
        "legislation_ref": "AASB 101",
    },
    {
        "rule_id": "GEN-06",
        "category": "general",
        "title": "Prior year adjustments detected",
        "description": (
            "Prior year adjustment accounts detected for {entity_name} totalling {total}. "
            "Review the nature and disclosure requirements."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["prior year adjustment", "prior period", "opening balance adjustment",
                                 "correction of error"],
            "threshold_value": 0,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "Review prior year adjustments. Material corrections of prior period errors "
            "must be disclosed in accordance with AASB 108. Consider restating comparatives."
        ),
        "legislation_ref": "AASB 108 (Accounting Policies, Changes in Estimates and Errors)",
    },
    {
        "rule_id": "GEN-07",
        "category": "general",
        "title": "Negative bank balance — potential overdraft",
        "description": (
            "Bank account with credit balance (negative cash) detected for {entity_name}. "
            "This may indicate a bank overdraft that should be reclassified as a current liability."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "balance_sign",
            "account_keywords": ["bank", "cash at bank", "cheque account", "savings account",
                                 "operating account"],
            "expected_sign": "debit",
        },
        "recommended_action": (
            "If the bank account has a credit balance, reclassify as a bank overdraft "
            "under current liabilities. Do not offset against other bank accounts "
            "unless a legal right of set-off exists."
        ),
        "legislation_ref": "AASB 101 para 32-35, AASB 107 para 8",
    },
    {
        "rule_id": "GEN-08",
        "category": "general",
        "title": "Provision for income tax review",
        "description": (
            "Income tax provision of {total} for {entity_name}. "
            "Verify the provision is correctly calculated based on taxable income."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["income tax", "tax provision", "provision for tax",
                                 "current tax liability"],
            "threshold_value": 0,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "Reconcile the income tax provision to the tax effect calculation. "
            "Verify the correct tax rate is applied (25% base rate entity or 30% full rate). "
            "For SMSFs, the rate is 15% (0% for pension phase)."
        ),
        "legislation_ref": "ITAA 1997 s23, AASB 112 (Income Taxes)",
    },
    {
        "rule_id": "GEN-09",
        "category": "general",
        "title": "Large rounding or adjustment entries",
        "description": (
            "Rounding or adjustment accounts totalling {total} for {entity_name}. "
            "Large rounding entries may indicate systematic errors."
        ),
        "severity": "LOW",
        "tier": 2,
        "applicable_entities": ["company", "trust", "partnership", "sole_trader", "smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["rounding", "adjustment", "write-off", "write off",
                                 "miscellaneous"],
            "threshold_value": 500,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "Investigate rounding and adjustment entries. Small rounding differences are normal, "
            "but large amounts may indicate posting errors or unreconciled items."
        ),
        "legislation_ref": "",
    },

    # -----------------------------------------------------------------------
    # SMSF-SPECIFIC (Rules SMSF-01 to SMSF-05)
    # -----------------------------------------------------------------------
    {
        "rule_id": "SMSF-01",
        "category": "smsf",
        "title": "SMSF — sole purpose test",
        "description": (
            "Review whether all investments and expenditure for {entity_name} satisfy "
            "the sole purpose test. Total expenses: {total}."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["expense", "cost", "payment", "fee"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Verify all fund expenditure is for the sole purpose of providing retirement benefits. "
            "Personal or non-arm's length expenses breach the sole purpose test."
        ),
        "legislation_ref": "SIS Act 1993 s62 (sole purpose test)",
    },
    {
        "rule_id": "SMSF-02",
        "category": "smsf",
        "title": "SMSF — non-concessional contribution cap",
        "description": (
            "Non-concessional contributions detected for {entity_name} totalling {total}. "
            "Verify contributions do not exceed the non-concessional cap."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["non-concessional", "after-tax contribution", "member contribution"],
            "threshold_key": "smsf_non_concessional_cap",
            "comparison": "gt",
        },
        "recommended_action": (
            "Review non-concessional contributions per member. Excess contributions "
            "attract a tax of 47% unless the member elects to withdraw them."
        ),
        "legislation_ref": "ITAA 1997 s292-85",
    },
    {
        "rule_id": "SMSF-03",
        "category": "smsf",
        "title": "SMSF — LRBA (limited recourse borrowing)",
        "description": (
            "Borrowing or LRBA-related accounts detected for {entity_name} totalling {total}. "
            "Verify the borrowing arrangement complies with s67A."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["lrba", "borrowing", "loan - property", "limited recourse",
                                 "instalment warrant"],
            "threshold_value": 0,
            "comparison": "abs_gt",
        },
        "recommended_action": (
            "Review the LRBA structure. Ensure: single acquirable asset in a holding trust, "
            "limited recourse terms, arm's length interest rate (if related party lender), "
            "and compliance with PCG 2016/5."
        ),
        "legislation_ref": "SIS Act 1993 s67A, PCG 2016/5",
    },
    {
        "rule_id": "SMSF-04",
        "category": "smsf",
        "title": "SMSF — pension payments compliance",
        "description": (
            "Pension payments detected for {entity_name} totalling {total}. "
            "Verify minimum pension payments have been made."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["pension", "income stream", "retirement benefit",
                                 "member payment"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Verify minimum pension payments have been made by 30 June. "
            "Failure to meet minimum drawdown requirements means the income stream "
            "is not a complying pension and the fund loses the tax exemption on pension assets."
        ),
        "legislation_ref": "SIS Reg 1.06(9A), SISR Sch 7",
    },
    {
        "rule_id": "SMSF-05",
        "category": "smsf",
        "title": "SMSF — investment strategy review",
        "description": (
            "Annual review of the investment strategy for {entity_name} is required. "
            "Verify the strategy is documented and investments are consistent with it."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["smsf"],
        "trigger_config": {
            "type": "account_threshold",
            "account_keywords": ["investment", "shares", "property", "managed fund",
                                 "term deposit", "crypto"],
            "threshold_value": 0,
            "comparison": "gt",
        },
        "recommended_action": (
            "Document the annual investment strategy review. Consider diversification, "
            "liquidity, risk, return, and the ability to meet member benefit obligations."
        ),
        "legislation_ref": "SIS Act 1993 s52(2)(f), SIS Reg 4.09",
    },

    # -----------------------------------------------------------------------
    # DIVISION 7A — UPGRADED MODULE (Rules T2-D7A-01 to T2-D7A-08)
    # Coordinated 8-rule detection engine replacing D7A-01–D7A-06.
    # These rules are executed by core.eva_div7a, not the generic risk_engine.
    # -----------------------------------------------------------------------
    {
        "rule_id": "T2-D7A-01",
        "category": "division_7a",
        "title": "Shareholder/Director Loan Debit Balance",
        "description": (
            "Director/shareholder loan account(s) for {entity_name} show a net debit "
            "balance of {total} at year end. This constitutes a loan under ss 109C–109D "
            "ITAA 1936 and is assessable as an unfranked deemed dividend unless a complying "
            "loan agreement is in place."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "div7a_module",
            "rule_category": "position_detection",
            "check_type": "debit_balance",
            "fires_on": "position",
        },
        "recommended_action": (
            "1. Execute a Div 7A complying loan agreement covering the full balance. "
            "2. Ensure agreement specifies benchmark interest rate. "
            "3. Calculate and verify minimum yearly repayment. "
            "4. Document the purpose of each drawdown in workpapers."
        ),
        "legislation_ref": "ITAA 1936 ss 109C–109D, s 109F, s 109N",
    },
    {
        "rule_id": "T2-D7A-02",
        "category": "division_7a",
        "title": "Loan Balance Increase (Escalation Modifier)",
        "description": (
            "Director/shareholder loan balance for {entity_name} has increased by {increase} "
            "from prior year. The complying agreement must cover the full current year balance."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "div7a_module",
            "rule_category": "position_detection",
            "check_type": "balance_increase",
            "fires_on": "movement",
            "enriches": "T2-D7A-01",
        },
        "recommended_action": (
            "Update the complying loan agreement to cover the increased balance. "
            "If increase exceeds $200,000, escalate to Elio per firm policy."
        ),
        "legislation_ref": "ITAA 1936 ss 109C–109D",
    },
    {
        "rule_id": "T2-D7A-03",
        "category": "division_7a",
        "title": "Payments to/for Shareholders (s 109E)",
        "description": (
            "Payments of a personal nature totalling {total} detected for {entity_name}. "
            "These may constitute deemed dividends under s 109E ITAA 1936 if paid to or on "
            "behalf of a shareholder/associate."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "div7a_module",
            "rule_category": "position_detection",
            "check_type": "s109e_payments",
            "fires_on": "position",
            "threshold": 5000,
        },
        "recommended_action": (
            "Review each personal expense account. Determine if amounts should be treated as "
            "Div 7A loans, deemed dividends, or FBT-reportable benefits. Aggregate per-shareholder."
        ),
        "legislation_ref": "ITAA 1936 s 109E",
    },
    {
        "rule_id": "T2-D7A-04",
        "category": "division_7a",
        "title": "Missing Complying Loan Agreement",
        "description": (
            "No complying Division 7A loan agreement on file for {entity_name} covering "
            "the {total} debit balance. Without an executed agreement, the full balance is "
            "treated as an unfranked deemed dividend."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "div7a_module",
            "rule_category": "compliance_verification",
            "check_type": "missing_agreement",
            "depends_on": "T2-D7A-01",
        },
        "recommended_action": (
            "Execute a Div 7A complying loan agreement before lodgement day. "
            "Use the Legal Document Wizard to generate the agreement."
        ),
        "legislation_ref": "ITAA 1936 s 109N",
    },
    {
        "rule_id": "T2-D7A-05",
        "category": "division_7a",
        "title": "Missing Benchmark Interest Income",
        "description": (
            "Benchmark interest shortfall for {entity_name}. Expected: {expected_interest} "
            "(opening balance × {benchmark_rate}%). Recorded: {recorded_interest}. Without "
            "benchmark interest, the loan agreement is non-compliant."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "div7a_module",
            "rule_category": "compliance_verification",
            "check_type": "interest_shortfall",
            "depends_on": "T2-D7A-01",
            "tolerance": 0.05,
        },
        "recommended_action": (
            "Record benchmark interest as assessable income (Item 8N). "
            "Ensure the interest rate matches the ATO benchmark rate for the relevant FY."
        ),
        "legislation_ref": "ITAA 1936 s 109F, s 109N, QC 17928",
    },
    {
        "rule_id": "T2-D7A-06",
        "category": "division_7a",
        "title": "Minimum Yearly Repayment Shortfall",
        "description": (
            "MYR shortfall for {entity_name}. Required: {expected_myr}. Actual: {actual_repayments}. "
            "Shortfall: {shortfall}. The shortfall amount is treated as a deemed unfranked dividend."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "div7a_module",
            "rule_category": "compliance_verification",
            "check_type": "myr_shortfall",
            "depends_on": "T2-D7A-01",
        },
        "recommended_action": (
            "Calculate MYR using s 109R formula. Confirm repayment made or will be made "
            "before 30 June. The shortfall is treated as a deemed dividend."
        ),
        "legislation_ref": "ITAA 1936 s 109R",
    },
    {
        "rule_id": "T2-D7A-07",
        "category": "division_7a",
        "title": "Unpaid Present Entitlements (Trust → Company)",
        "description": (
            "Unpaid present entitlement of {upe_amount} from {trust_name} to {entity_name}. "
            "Post-2022 UPEs are treated as Div 7A loans under TD 2022/11."
        ),
        "severity": "CRITICAL",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "div7a_module",
            "rule_category": "cross_entity",
            "check_type": "upe_detection",
            "cross_entity": True,
        },
        "recommended_action": (
            "For post-2022 UPEs: execute complying 7-year loan agreement or repay before "
            "lodgement day. For pre-2022 UPEs: confirm sub-trust arrangement per PS LA 2010/4."
        ),
        "legislation_ref": "ITAA 1936 s 109XA–109XB, TD 2022/11, PS LA 2010/4",
    },
    {
        "rule_id": "T2-D7A-08",
        "category": "division_7a",
        "title": "Interposed Entity Loans (ss 109T–109V)",
        "description": (
            "Potential interposed entity arrangement: {entity_name} has a receivable from "
            "{intermediary_name} which has a relationship with {shareholder_name}. Division 7A "
            "may apply under ss 109T–109V."
        ),
        "severity": "HIGH",
        "tier": 2,
        "applicable_entities": ["company"],
        "trigger_config": {
            "type": "div7a_module",
            "rule_category": "cross_entity",
            "check_type": "interposed_entity",
            "cross_entity": True,
        },
        "recommended_action": (
            "Review the interposed entity provisions. Determine if the ultimate beneficiary "
            "is a shareholder or associate. This requires manual review — the detection is advisory."
        ),
        "legislation_ref": "ITAA 1936 ss 109T–109V",
    },
]
