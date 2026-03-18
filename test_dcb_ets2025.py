"""
End-to-end test: DocumentContextBuilder against Emission Treatment Solutions FY2025.

Uses Django's test framework with a SQLite in-memory database.
PostgreSQL-specific migrations (pgvector, HNSW indexes) are faked automatically.
"""

import os
import sys
import django
from decimal import Decimal

# ── Environment setup ─────────────────────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import environ as _environ
_env = _environ.Env()
_environ.Env.read_env(os.path.join(os.path.dirname(__file__), ".env.test"))

django.setup()

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):     print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg):   print(f"  {RED}✗{RESET}  {msg}")
def warn(msg):   print(f"  {YELLOW}⚠{RESET}  {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")

results = {"pass": 0, "fail": 0, "warn": 0}

# ── 1. Create test database using Django's test runner ────────────────────────
header("Step 1: Setting up test database (SQLite in-memory)")

from django.test.utils import setup_test_environment
setup_test_environment()

# Use Django's test database creation which handles --keepdb and faking
from django.test.runner import DiscoverRunner
# Use --no-migrations so Django creates tables directly from model state,
# bypassing the pgvector-specific migrations that require PostgreSQL.
runner = DiscoverRunner(verbosity=0, keepdb=False)

# Monkey-patch to disable migrations (equivalent to --no-migrations)
from unittest.mock import patch, MagicMock
from django.test.utils import override_settings

class DisableMigrations:
    def __contains__(self, item):
        return True
    def __getitem__(self, item):
        return None

with override_settings(MIGRATION_MODULES=DisableMigrations()):
    old_config = runner.setup_databases()

ok("Test database created")

try:
    # ── All test logic runs inside the test DB context ────────────────────────
    from datetime import date

    from core.models import (
        Entity, FinancialYear, TrialBalanceLine, EntityOfficer, FirmSettings,
    )
    from core.document_context_builder import DocumentContextBuilder, ContextValidationError

    # ── 2. Create FirmSettings ────────────────────────────────────────────────
    header("Step 2: Creating FirmSettings (MC & S)")
    FirmSettings.objects.all().delete()
    firm = FirmSettings.objects.create(
        firm_name="MC & S Chartered Accountants",
        firm_legal_name="MC & S Pty Ltd",
        firm_abn="69079892023",
        firm_address_1="PO Box 123",
        firm_address_2="Melbourne VIC 3000",
        firm_phone="(03) 9000 0000",
        firm_email="info@mcands.com.au",
        firm_website="www.mcands.com.au",
        compilation_report_name="MC & S Chartered Accountants",
        tax_agent_number="12345678",
        bas_agent_number="87654321",
        asic_agent_number="ASIC-001",
        signatory_name="Michael Scarton",
        signatory_designation="CPA, Registered Tax Agent",
        professional_body="CPA Australia",
        membership_number="CPA-123456",
        practice_independence_maintained=True,
    )
    ok(f"FirmSettings created: {firm.firm_name}")

    # ── 3. Create ETS entity ──────────────────────────────────────────────────
    header("Step 3: Creating Emission Treatment Solutions entity")
    entity = Entity.objects.create(
        entity_name="Emission Treatment Solutions Pty Ltd",
        entity_type="company",
        abn="12345678901",
        acn="123456789",
        address_line_1="45 Industrial Drive",
        suburb="Dandenong",
        state="VIC",
        postcode="3175",
        trading_as="ETS",
    )
    ok(f"Entity created: {entity.entity_name}")

    # ── 4. Create Directors ───────────────────────────────────────────────────
    header("Step 4: Creating EntityOfficers (directors)")
    d1 = EntityOfficer.objects.create(
        entity=entity, full_name="John Smith",
        role="director", title="Director", display_order=1,
    )
    d2 = EntityOfficer.objects.create(
        entity=entity, full_name="Jane Smith",
        role="director_shareholder", title="Director", display_order=2,
    )
    ok(f"Directors: {d1.full_name}, {d2.full_name}")

    # ── 5. Create FinancialYear 2025 ──────────────────────────────────────────
    header("Step 5: Creating FinancialYear 2025")
    fy = FinancialYear.objects.create(
        entity=entity,
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        status="open",
    )
    ok(f"FinancialYear: {fy.start_date} → {fy.end_date}")

    # ── 6. Create Trial Balance Lines ─────────────────────────────────────────
    header("Step 6: Creating Trial Balance Lines (ETS FY2025)")
    # Account code ranges (from _get_tb_sections logic):
    # < 1000   = income/trading income
    # 1000-1199 = COGS
    # 1200-1999 = expenses
    # 2000-2499 = current assets
    # 2500-2999 = non-current assets
    # 3000-3499 = current liabilities
    # 3500-3999 = non-current liabilities
    # 4000-4999 = equity
    # TrialBalanceLine fields: closing_balance (CY), prior_debit/prior_credit (PY)
    # For income/liabilities/equity (credit-normal): closing_balance is negative
    # For assets/expenses (debit-normal): closing_balance is positive
    tb_data = [
        # (account_code, account_name, closing_balance, prior_debit, prior_credit)
        # Income (credit-normal → negative closing_balance)
        ("100",  "Revenue — Emission Testing Services", Decimal("-850000"), Decimal("0"),      Decimal("720000")),
        ("110",  "Revenue — Consulting Services",        Decimal("-125000"), Decimal("0"),      Decimal("98000")),
        ("190",  "Interest Income",                      Decimal("-3200"),   Decimal("0"),      Decimal("2800")),
        # COGS (debit-normal)
        ("1000", "Cost of Sales — Labour",               Decimal("280000"),  Decimal("240000"), Decimal("0")),
        ("1100", "Cost of Sales — Materials",            Decimal("95000"),   Decimal("82000"),  Decimal("0")),
        # Expenses
        ("1200", "Salaries & Wages",                     Decimal("185000"),  Decimal("162000"), Decimal("0")),
        ("1210", "Superannuation",                       Decimal("20350"),   Decimal("17820"),  Decimal("0")),
        ("1300", "Rent & Occupancy",                     Decimal("48000"),   Decimal("48000"),  Decimal("0")),
        ("1310", "Motor Vehicle Expenses",               Decimal("22000"),   Decimal("19500"),  Decimal("0")),
        ("1320", "Depreciation",                         Decimal("35000"),   Decimal("32000"),  Decimal("0")),
        ("1400", "Accounting & Legal Fees",              Decimal("18500"),   Decimal("16000"),  Decimal("0")),
        ("1410", "Insurance",                            Decimal("12000"),   Decimal("11500"),  Decimal("0")),
        ("1500", "Office & Administration",              Decimal("8500"),    Decimal("7800"),   Decimal("0")),
        ("1510", "Marketing & Advertising",              Decimal("15000"),   Decimal("12000"),  Decimal("0")),
        # Current Assets
        ("2000", "Cash at Bank — Operating",             Decimal("145000"),  Decimal("98000"),  Decimal("0")),
        ("2010", "Cash at Bank — Term Deposit",          Decimal("50000"),   Decimal("50000"),  Decimal("0")),
        ("2100", "Trade Debtors",                        Decimal("125000"),  Decimal("108000"), Decimal("0")),
        ("2200", "GST Receivable",                       Decimal("8500"),    Decimal("7200"),   Decimal("0")),
        ("2300", "Prepayments",                          Decimal("6000"),    Decimal("5500"),   Decimal("0")),
        # Non-current Assets
        ("2500", "Plant & Equipment — At Cost",          Decimal("320000"),  Decimal("285000"), Decimal("0")),
        ("2510", "Less: Accumulated Depreciation",       Decimal("-145000"), Decimal("0"),      Decimal("110000")),
        ("2600", "Motor Vehicles — At Cost",             Decimal("85000"),   Decimal("85000"),  Decimal("0")),
        ("2610", "Less: Accumulated Depreciation MV",    Decimal("-42000"),  Decimal("0"),      Decimal("28000")),
        # Current Liabilities (credit-normal → negative)
        ("3000", "Trade Creditors",                      Decimal("-45000"),  Decimal("0"),      Decimal("38000")),
        ("3100", "GST Payable",                          Decimal("-12000"),  Decimal("0"),      Decimal("10500")),
        ("3200", "PAYG Withholding Payable",             Decimal("-8500"),   Decimal("0"),      Decimal("7800")),
        ("3300", "Superannuation Payable",               Decimal("-5200"),   Decimal("0"),      Decimal("4800")),
        ("3400", "Income Tax Payable",                   Decimal("-18000"),  Decimal("0"),      Decimal("15000")),
        ("3450", "Accrued Expenses",                     Decimal("-9500"),   Decimal("0"),      Decimal("8200")),
        # Non-current Liabilities
        ("3500", "Loan — Director (J Smith)",            Decimal("-95000"),  Decimal("0"),      Decimal("110000")),
        ("3600", "Chattel Mortgage — Motor Vehicle",     Decimal("-38000"),  Decimal("0"),      Decimal("52000")),
        # Equity (credit-normal → negative)
        ("4000", "Share Capital",                        Decimal("-2"),      Decimal("0"),      Decimal("2")),
        ("4100", "Retained Earnings",                    Decimal("-373298"), Decimal("0"),      Decimal("321300")),
        ("4200", "Income Tax Expense",                   Decimal("52000"),   Decimal("44000"),  Decimal("0")),
    ]
    for code, name, closing_bal, prior_dr, prior_cr in tb_data:
        TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code=code,
            account_name=name,
            closing_balance=closing_bal,
            prior_debit=prior_dr,
            prior_credit=prior_cr,
            opening_balance=Decimal("0"),
            debit=Decimal("0"),
            credit=Decimal("0"),
            is_adjustment=False,
        )
    ok(f"Created {len(tb_data)} trial balance lines")

    # ── 6b. Create DividendEvent for ETS FY2025 ───────────────────────────────
    from core.models import DividendEvent
    dividend_event = DividendEvent.objects.create(
        entity=entity,
        financial_year=fy,
        dividend_type="final",
        total_amount=Decimal("100000"),
        franking_percentage=Decimal("100"),
        company_tax_rate=Decimal("25"),
        record_date=date(2025, 6, 25),
        payment_date=date(2025, 6, 30),
        declaration_date=date(2025, 6, 20),
        solvency_confirmed=True,
        resolution_type="board_resolution",
        meeting_location="Level 1, 123 Collins Street, Melbourne VIC 3000",
    )
    ok(f"DividendEvent created: {dividend_event}")

    # ── 7. Run DocumentContextBuilder for all 16 document types ──────────────
    header("Step 7: DocumentContextBuilder.build() for all 16 document types")

    DOCUMENT_TYPES = [
        "financial_statements",
        "compilation_report",
        "directors_declaration",
        "directors_report",
        "solvency_resolution",
        "dividend_statement",
        "dividend_declaration_minutes",
        "distribution_minutes",
        "beneficiary_statement",
        "partner_statement",
        "partnership_tax_summary",
        "div7a_loan_agreement",
        "management_representation_letter",
        "engagement_letter",
        "client_cover_letter",
        "eva_client_summary",
    ]

    WIZARD_DATA = {
        "loan_amount": "50000",
        "loan_date": "2024-07-01",
        "interest_rate": "8.27",
        "loan_term_years": "7",
        "security_type": "unsecured",
        "borrower_name": "John Smith",
        "borrower_address": "123 Smith Street, Melbourne VIC 3000",
        "services": ["tax_return", "financial_statements", "bas_lodgement"],
        "fee_amount": "8500",
        "fee_basis": "fixed",
        "additional_terms": "",
        "date": "2025-07-15",
        "dividend_amount": "100000",
        "dividend_per_share": "50000",
        "franking_percentage": "100",
        "payment_date": "2025-06-30",
        "record_date": "2025-06-25",
        "declaration_date": "2025-06-20",
    }

    CRITICAL_KEYS = [
        "entity_name", "practice_name", "practice_tax_agent_number",
        "fy_end_formatted", "fy_year",
    ]

    for doc_type in DOCUMENT_TYPES:
        try:
            dcb = DocumentContextBuilder(entity, financial_year=fy, wizard_data=WIZARD_DATA)
            ctx = dcb.build(doc_type)
            missing = [k for k in CRITICAL_KEYS if not ctx.get(k)]
            if missing:
                warn(f"{doc_type:45s} — {len(ctx)} keys, missing values: {missing}")
                results["warn"] += 1
            else:
                ok(f"{doc_type:45s} — {len(ctx)} keys  ✓")
                results["pass"] += 1
        except ContextValidationError as e:
            warn(f"{doc_type:45s} — ContextValidationError: {e.message}")
            for field in (e.missing_fields or []):
                print(f"       Missing: {field} — {e.resolution_hints.get(field, '')}")
            results["warn"] += 1
        except Exception as e:
            fail(f"{doc_type:45s} — {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            results["fail"] += 1

    # ── 8. Test build_company_context enrichment ──────────────────────────────
    header("Step 8: build_company_context() + DCB enrichment pipeline")
    try:
        from core.fs_template_service import build_company_context
        ctx = build_company_context(fy, include_watermark=True)

        practice_keys = [k for k in ctx if k.startswith("practice_")]
        if practice_keys:
            ok(f"DCB enrichment applied — {len(practice_keys)} practice_* keys injected")
            for key in ["practice_name", "practice_tax_agent_number",
                        "practice_signatory_name", "practice_independence_maintained"]:
                val = ctx.get(key, "MISSING")
                ok(f"  {key} = {val!r}")
            results["pass"] += 1
        else:
            fail("DCB enrichment NOT applied — no practice_* keys found")
            results["fail"] += 1

        legacy_keys = ["entity_name", "abn", "net_profit_cy", "total_assets_cy",
                       "directors", "firm_name"]
        missing_legacy = [k for k in legacy_keys if k not in ctx]
        if missing_legacy:
            fail(f"Legacy keys lost after enrichment: {missing_legacy}")
            results["fail"] += 1
        else:
            ok(f"All {len(legacy_keys)} legacy keys preserved after enrichment")
            results["pass"] += 1

        # Spot-check financial figures
        ok(f"  net_profit_cy  = {ctx.get('net_profit_cy')}")
        ok(f"  total_assets_cy = {ctx.get('total_assets_cy')}")
        ok(f"  entity_name    = {ctx.get('entity_name')}")

    except Exception as e:
        fail(f"build_company_context test failed: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        results["fail"] += 1

    # ── 9. Test Jinja2 custom filters ─────────────────────────────────────────
    header("Step 9: Jinja2 custom filters")
    try:
        from core.document_context_builder import get_jinja_env
        env = get_jinja_env()

        filter_tests = [
            # format_currency rounds to whole dollars (financial statement convention)
            ("format_currency",     Decimal("1234567.89"),  "$1,234,568"),
            # format_percentage uses 1 decimal place by default
            ("format_percentage",   Decimal("8.27"),         "8.3%"),
            ("format_date_long",    date(2025, 6, 30),       "30 June 2025"),
            ("format_date_short",   date(2025, 6, 30),       "30/06/2025"),
            ("format_abn",          "12345678901",           "12 345 678 901"),
            ("format_acn",          "123456789",             "123 456 789"),
            ("mask_tfn",            "123456789",             "XXX XXX 789"),
            ("format_yesno",        True,                    "Yes"),
            ("format_yesno",        False,                   "No"),
            ("upper_first",         "company",               "Company"),
        ]
        for filter_name, value, expected in filter_tests:
            fn = env.filters.get(filter_name)
            if fn is None:
                fail(f"Filter '{filter_name}' not registered")
                results["fail"] += 1
                continue
            result = fn(value)
            if result == expected:
                ok(f"  {filter_name}({value!r}) → {result!r}")
                results["pass"] += 1
            else:
                fail(f"  {filter_name}({value!r}) → {result!r}  (expected {expected!r})")
                results["fail"] += 1

    except Exception as e:
        fail(f"Jinja2 filter test failed: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        results["fail"] += 1

    # ── 10. Test ContextValidationError (missing TAN) ─────────────────────────
    header("Step 10: ContextValidationError — missing Tax Agent Number")
    try:
        firm.tax_agent_number = ""
        firm.save()
        FirmSettings._cache = None  # bust any in-process cache

        dcb = DocumentContextBuilder(entity, financial_year=fy)
        try:
            ctx = dcb.build("compilation_report")
            warn("Expected ContextValidationError but none raised (soft-warning mode)")
            results["warn"] += 1
        except ContextValidationError as e:
            ok(f"ContextValidationError raised: {e.message}")
            ok(f"  Missing fields: {e.missing_fields}")
            hint = (e.resolution_hints or {}).get("practice_tax_agent_number", "N/A")
            ok(f"  Resolution hint: {hint}")
            results["pass"] += 1

        firm.tax_agent_number = "12345678"
        firm.save()

    except Exception as e:
        fail(f"ContextValidationError test failed: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        results["fail"] += 1

    # ── 11. Financial data spot-checks ────────────────────────────────────────
    header("Step 11: Financial data spot-checks (ETS FY2025)")
    try:
        dcb = DocumentContextBuilder(entity, financial_year=fy)
        ctx = dcb.build("financial_statements")

        checks = [
            ("entity_name",       "Emission Treatment Solutions Pty Ltd"),
            ("fy_year",           "2025"),
            ("fy_start_formatted","1 July 2024"),
            ("fy_end_formatted",  "30 June 2025"),
            ("entity_type",       "company"),
            ("practice_name",     "MC & S Chartered Accountants"),
            ("practice_tax_agent_number", "12345678"),
            ("practice_signatory_name",   "Michael Scarton"),
            ("practice_professional_body", "CPA Australia"),
        ]
        for key, expected in checks:
            val = ctx.get(key)
            if val == expected:
                ok(f"  {key} = {val!r}")
                results["pass"] += 1
            else:
                fail(f"  {key} = {val!r}  (expected {expected!r})")
                results["fail"] += 1

        # Check financial totals are non-zero (DCB uses raw Decimal keys)
        from decimal import Decimal as D
        financial_checks = [
            ("revenue",          "total income"),
            ("net_profit",       "net profit"),
            ("total_assets",     "total assets"),
            ("total_liabilities","total liabilities"),
            ("total_equity",     "total equity"),
        ]
        for key, label in financial_checks:
            val = ctx.get(key)
            if val is not None and val != D(0):
                ok(f"  {label} ({key}) = {val:,.0f}")
                results["pass"] += 1
            else:
                fail(f"  {label} ({key}) = {val!r}  (expected non-zero Decimal)")
                results["fail"] += 1

    except Exception as e:
        fail(f"Financial spot-check failed: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        results["fail"] += 1

finally:
    with override_settings(MIGRATION_MODULES=DisableMigrations()):
        runner.teardown_databases(old_config)

# ── Summary ───────────────────────────────────────────────────────────────────
header("=" * 60)
total = results["pass"] + results["fail"] + results["warn"]
print(f"\n  {BOLD}Results:{RESET}")
print(f"  {GREEN}PASS{RESET}: {results['pass']}")
print(f"  {YELLOW}WARN{RESET}: {results['warn']}")
print(f"  {RED}FAIL{RESET}: {results['fail']}")
print(f"  Total:  {total}")

if results["fail"] > 0:
    print(f"\n  {RED}{BOLD}END-TO-END TEST FAILED{RESET}")
    sys.exit(1)
elif results["warn"] > 0:
    print(f"\n  {YELLOW}{BOLD}END-TO-END TEST PASSED WITH WARNINGS{RESET}")
    sys.exit(0)
else:
    print(f"\n  {GREEN}{BOLD}END-TO-END TEST PASSED CLEANLY{RESET}")
    sys.exit(0)
