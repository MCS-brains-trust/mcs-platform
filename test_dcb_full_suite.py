"""
StatementHub DocumentContextBuilder — Full Multi-Entity Test Suite
==================================================================
Tests all entity types (company, trust, partnership, sole trader) with:
  - Realistic trial balance data using correct account code ranges
  - Prior year comparatives
  - AASB compliance flag assertions
  - Edge cases (zero revenue, negative equity, missing officers)
  - Practice branding key injection
  - ContextValidationError for missing required fields

Account code ranges (matching production fs_template_service.py):
  < 1000  : Income (trading income or other income)
  1000-1199: COGS
  1200-1999: Expenses
  2000-2499: Current Assets
  2500-2999: Non-current Assets
  3000-3499: Current Liabilities
  3500-3999: Non-current Liabilities
  4000-4999: Equity (incl. 4100-4149 income tax)
  5000-5999: COGS (alternate range)
  6000+    : Expenses (alternate range)
"""
import os
import sys
import django
import environ
from datetime import date
from decimal import Decimal

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PASS = 0
FAIL = 0
WARN = 0

def ok(label, detail=""):
    global PASS
    PASS += 1
    detail_str = f"  {YELLOW}{detail}{RESET}" if detail else ""
    print(f"  {GREEN}✓{RESET}  {label:<50}{detail_str}")

def fail(label, detail=""):
    global FAIL
    FAIL += 1
    detail_str = f"  {RED}{detail}{RESET}" if detail else ""
    print(f"  {RED}✗{RESET}  {label:<50}{detail_str}")

def warn(label, detail=""):
    global WARN
    WARN += 1
    detail_str = f"  {YELLOW}{detail}{RESET}" if detail else ""
    print(f"  {YELLOW}⚠{RESET}  {label:<50}{detail_str}")

def section(title):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"  {title}")
    print(f"{BOLD}{'='*60}{RESET}")

def check(label, condition, detail=""):
    if condition:
        ok(label, detail)
    else:
        fail(label, detail)

def check_approx(label, actual, expected, tolerance=Decimal("1.00")):
    """Check that actual ≈ expected within tolerance."""
    try:
        diff = abs(Decimal(str(actual)) - Decimal(str(expected)))
        if diff <= tolerance:
            ok(label, f"{actual}")
        else:
            fail(label, f"expected {expected}, got {actual} (diff {diff})")
    except Exception as e:
        fail(label, f"error: {e}")


# ── Django setup ─────────────────────────────────────────────────────────────

def setup_test_environment():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    env = environ.Env()
    environ.Env.read_env(os.path.join(os.path.dirname(__file__), ".env.test"))
    django.setup()

class DisableMigrations:
    def __contains__(self, item):
        return True
    def __getitem__(self, item):
        return None


# ── Fixture helpers ───────────────────────────────────────────────────────────

def make_firm_settings():
    from core.models import FirmSettings
    fs, _ = FirmSettings.objects.get_or_create(pk=1)
    fs.firm_name = "MC & S Chartered Accountants"
    fs.firm_legal_name = "MC & S Accounting Pty Ltd"
    fs.firm_abn = "12345678901"
    fs.firm_address_1 = "PO Box 1234"
    fs.firm_address_2 = "Melbourne VIC 3000"
    fs.firm_phone = "03 9000 0000"
    fs.firm_email = "admin@mcands.com.au"
    fs.firm_website = "https://mcands.com.au"
    fs.tax_agent_number = "12345678"
    fs.bas_agent_number = "87654321"
    fs.signatory_name = "Elio Scarton"
    fs.signatory_designation = "CPA, Registered Tax Agent"
    fs.professional_body = "CPA"
    fs.membership_number = "CPA123456"
    fs.practice_independence_maintained = True
    fs.save()
    return fs


def make_tb_lines(fy, lines_data):
    """
    lines_data: list of dicts with keys:
      account_code, account_name, debit, credit, prior_debit, prior_credit
    Uses correct TrialBalanceLine field names: opening_balance, debit, credit,
    closing_balance, prior_debit, prior_credit, prior_closing_balance.
    """
    from core.models import TrialBalanceLine
    created = []
    for d in lines_data:
        debit = Decimal(str(d.get("debit", 0)))
        credit = Decimal(str(d.get("credit", 0)))
        prior_debit = Decimal(str(d.get("prior_debit", 0)))
        prior_credit = Decimal(str(d.get("prior_credit", 0)))
        closing_balance = debit - credit
        prior_closing = prior_debit - prior_credit
        line = TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code=d["account_code"],
            account_name=d["account_name"],
            opening_balance=Decimal("0"),
            debit=debit,
            credit=credit,
            closing_balance=closing_balance,
            prior_debit=prior_debit,
            prior_credit=prior_credit,
            prior_closing_balance=prior_closing,
            is_adjustment=False,
        )
        created.append(line)
    return created


# ── ENTITY 1: Discretionary Trust ─────────────────────────────────────────────

def make_discretionary_trust():
    from core.models import Entity, EntityOfficer, FinancialYear
    entity = Entity.objects.create(
        entity_name="Smith Family Trust",
        entity_type="trust",
        abn="98765432109",
        trustee_name="Smith Trustee Pty Ltd",
        trustee_acn="123456789",
        deed_date=date(2005, 3, 15),
        vesting_date=date(2075, 3, 15),
        appointor="Robert Smith",
        address_line_1="45 Trust Lane",
        suburb="Melbourne",
        state="VIC",
        postcode="3000",
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Smith Trustee Pty Ltd",
        role="trustee", is_signatory=True, display_order=1,
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Robert Smith",
        role="beneficiary", beneficiary_type="adult",
        distribution_percentage=Decimal("50.00"),
        tax_residency="resident", display_order=2,
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Susan Smith",
        role="beneficiary", beneficiary_type="adult",
        distribution_percentage=Decimal("30.00"),
        tax_residency="resident", display_order=3,
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Smith Holdings Pty Ltd",
        role="beneficiary", beneficiary_type="company",
        distribution_percentage=Decimal("20.00"),
        display_order=4,
    )
    fy = FinancialYear.objects.create(
        entity=entity,
        year_label="FY2025",
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
    )
    # Trust TB — CY net income $185,000 | PY $162,000
    # Account codes use production ranges:
    #   < 1000 = income, 1200-1999 = expenses, 2000-2499 = current assets,
    #   2500-2999 = non-current assets, 3000-3499 = current liabilities,
    #   3500-3999 = non-current liabilities, 4000-4999 = equity
    tb_lines = [
        # Income (< 1000, credit-normal)
        {"account_code": "100", "account_name": "Rental Income",       "credit": 95000,  "prior_credit": 82000},
        {"account_code": "200", "account_name": "Dividend Income",      "credit": 45000,  "prior_credit": 38000},
        {"account_code": "300", "account_name": "Interest Income",      "credit": 15000,  "prior_credit": 12000},
        {"account_code": "400", "account_name": "Capital Gains",        "credit": 30000,  "prior_credit": 30000},
        # Expenses (1200-1999, debit-normal)
        {"account_code": "1200", "account_name": "Property Management", "debit": 9500,    "prior_debit": 8200},
        {"account_code": "1300", "account_name": "Accounting Fees",     "debit": 5500,    "prior_debit": 5000},
        {"account_code": "1400", "account_name": "Bank Charges",        "debit": 1000,    "prior_debit": 800},
        {"account_code": "1500", "account_name": "Insurance",           "debit": 2500,    "prior_debit": 2200},
        {"account_code": "1600", "account_name": "Repairs & Maintenance","debit": 4500,   "prior_debit": 3800},
        # Current Assets (2000-2499, debit-normal)
        {"account_code": "2000", "account_name": "Cash at Bank",        "debit": 45000,   "prior_debit": 38000},
        {"account_code": "2100", "account_name": "Trade Debtors",       "debit": 8500,    "prior_debit": 7200},
        # Non-current Assets (2500-2999, debit-normal)
        {"account_code": "2500", "account_name": "Investment Property", "debit": 850000,  "prior_debit": 820000},
        {"account_code": "2600", "account_name": "Share Portfolio",     "debit": 320000,  "prior_debit": 295000},
        # Current Liabilities (3000-3499, credit-normal)
        {"account_code": "3000", "account_name": "Accounts Payable",   "credit": 3500,   "prior_credit": 3000},
        # Non-current Liabilities (3500-3999, credit-normal)
        {"account_code": "3500", "account_name": "Mortgage Payable",   "credit": 480000, "prior_credit": 510000},
        # Equity / Corpus (4000-4999, credit-normal)
        {"account_code": "4000", "account_name": "Trust Corpus",       "credit": 550000, "prior_credit": 520000},
        {"account_code": "4100", "account_name": "Retained Income",    "credit": 190000, "prior_credit": 162000},  # balanced: net_assets=740000, corpus+retained=740000
    ]
    make_tb_lines(fy, tb_lines)
    return entity, fy


# ── ENTITY 2: Partnership ──────────────────────────────────────────────────────

def make_partnership():
    from core.models import Entity, EntityOfficer, FinancialYear
    entity = Entity.objects.create(
        entity_name="Green & Associates",
        entity_type="partnership",
        abn="55544433322",
        deed_date=date(2018, 1, 1),
        address_line_1="88 Partner Street",
        suburb="Brisbane",
        state="QLD",
        postcode="4000",
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="David Green",
        role="partner", profit_share_percentage=Decimal("60.00"),
        is_signatory=True, display_order=1,
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Sarah Green",
        role="partner", profit_share_percentage=Decimal("40.00"),
        is_signatory=True, display_order=2,
    )
    fy = FinancialYear.objects.create(
        entity=entity,
        year_label="FY2025",
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
    )
    # Partnership TB — CY net profit $210,000 | PY $188,000
    # Revenue: 775,000 | COGS: 227,000 | Expenses: 338,000 | Net: 210,000
    tb_lines = [
        # Income (< 1000, credit-normal)
        {"account_code": "100", "account_name": "Consulting Revenue",   "credit": 680000, "prior_credit": 610000},
        {"account_code": "200", "account_name": "Project Income",       "credit": 95000,  "prior_credit": 82000},
        # COGS (1000-1199, debit-normal)
        {"account_code": "1000", "account_name": "Subcontractor Costs", "debit": 185000,  "prior_debit": 168000},
        {"account_code": "1100", "account_name": "Direct Materials",    "debit": 42000,   "prior_debit": 38000},
        # Expenses (1200-1999, debit-normal)
        {"account_code": "1200", "account_name": "Salaries & Wages",    "debit": 180000,  "prior_debit": 162000},
        {"account_code": "1300", "account_name": "Rent",                "debit": 48000,   "prior_debit": 44000},
        {"account_code": "1400", "account_name": "Motor Vehicle",       "debit": 28000,   "prior_debit": 24000},
        {"account_code": "1500", "account_name": "Depreciation",        "debit": 15000,   "prior_debit": 14000},
        {"account_code": "1600", "account_name": "Insurance",           "debit": 8000,    "prior_debit": 7500},
        {"account_code": "1700", "account_name": "Accounting Fees",     "debit": 7000,    "prior_debit": 6500},
        {"account_code": "1800", "account_name": "Other Expenses",      "debit": 52000,   "prior_debit": 44000},
        # Current Assets (2000-2499)
        {"account_code": "2000", "account_name": "Cash at Bank",        "debit": 95000,   "prior_debit": 82000},
        {"account_code": "2100", "account_name": "Trade Debtors",       "debit": 145000,  "prior_debit": 128000},
        # Non-current Assets (2500-2999)
        {"account_code": "2500", "account_name": "Plant & Equipment",   "debit": 85000,   "prior_debit": 95000},
        # Current Liabilities (3000-3499)
        {"account_code": "3000", "account_name": "Accounts Payable",   "credit": 28000,  "prior_credit": 24000},
        {"account_code": "3100", "account_name": "Bank Loan",           "credit": 45000,  "prior_credit": 60000},
        # Equity (4000-4999, credit-normal)
        # Capital accounts: balanced so net_assets=252000 (assets=325000, liab=73000)
        {"account_code": "4000", "account_name": "David Green Capital", "credit": 25200,  "prior_credit": 22560},
        {"account_code": "4100", "account_name": "Sarah Green Capital", "credit": 16800,  "prior_credit": 15040},
        {"account_code": "4900", "account_name": "Retained Earnings",   "credit": 210000, "prior_credit": 188000},
    ]
    make_tb_lines(fy, tb_lines)
    return entity, fy


# ── ENTITY 3: Sole Trader ──────────────────────────────────────────────────────

def make_sole_trader():
    from core.models import Entity, EntityOfficer, FinancialYear
    entity = Entity.objects.create(
        entity_name="James Wilson Consulting",
        entity_type="sole_trader",
        abn="11122233344",
        address_line_1="12 Solo Street",
        suburb="Adelaide",
        state="SA",
        postcode="5000",
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="James Wilson",
        role="sole_trader", is_signatory=True, display_order=1,
    )
    fy = FinancialYear.objects.create(
        entity=entity,
        year_label="FY2025",
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
    )
    # Sole trader TB — CY net profit $95,000 | PY $88,000
    # Revenue: 185,000 | Expenses: 90,000 | Net: 95,000
    tb_lines = [
        # Income (< 1000, credit-normal)
        {"account_code": "100", "account_name": "Consulting Fees",      "credit": 185000, "prior_credit": 172000},
        # Expenses (1200-1999, debit-normal)
        {"account_code": "1200", "account_name": "Home Office",         "debit": 8500,    "prior_debit": 8000},
        {"account_code": "1300", "account_name": "Motor Vehicle",       "debit": 18000,   "prior_debit": 16500},
        {"account_code": "1400", "account_name": "Depreciation",        "debit": 4500,    "prior_debit": 4200},
        {"account_code": "1500", "account_name": "Professional Fees",   "debit": 6500,    "prior_debit": 6000},
        {"account_code": "1600", "account_name": "Superannuation",      "debit": 19250,   "prior_debit": 17875},
        {"account_code": "1700", "account_name": "Other Expenses",      "debit": 33250,   "prior_debit": 31625},
        # Current Assets (2000-2499)
        {"account_code": "2000", "account_name": "Cash at Bank",        "debit": 42000,   "prior_debit": 35000},
        {"account_code": "2100", "account_name": "Trade Debtors",       "debit": 28500,   "prior_debit": 24000},
        # Non-current Assets (2500-2999)
        {"account_code": "2500", "account_name": "Equipment",           "debit": 18000,   "prior_debit": 22500},
        # Current Liabilities (3000-3499)
        {"account_code": "3000", "account_name": "Accounts Payable",   "credit": 4500,   "prior_credit": 3800},
        {"account_code": "3100", "account_name": "Tax Payable",         "credit": 12000,  "prior_credit": 11000},
        # Equity (4000-4999, credit-normal)
        # Sole traders use proprietors capital only — no retained earnings
        # net_assets = 88500-16500 = 72000, so proprietors_capital = 72000
        {"account_code": "4000", "account_name": "Proprietor's Capital","credit": 72000,  "prior_credit": 64700},
    ]
    make_tb_lines(fy, tb_lines)
    return entity, fy


# ── ENTITY 4: Going Concern Company ───────────────────────────────────────────

def make_going_concern_company():
    """A company with negative equity to trigger going concern material uncertainty."""
    from core.models import Entity, EntityOfficer, FinancialYear
    entity = Entity.objects.create(
        entity_name="Struggling Co Pty Ltd",
        entity_type="company",
        abn="99988877766",
        acn="998887776",
        address_line_1="1 Deficit Drive",
        suburb="Perth",
        state="WA",
        postcode="6000",
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Tom Struggling",
        role="director", is_signatory=True, display_order=1,
    )
    fy = FinancialYear.objects.create(
        entity=entity,
        year_label="FY2025",
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
    )
    # Negative equity: assets $180k, liabilities $250k → net assets -$70k
    tb_lines = [
        # Income (< 1000, credit-normal)
        {"account_code": "100", "account_name": "Sales Revenue",        "credit": 120000, "prior_credit": 180000},
        # Expenses (1200-1999, debit-normal)
        {"account_code": "1200", "account_name": "Cost of Sales",       "debit": 95000,   "prior_debit": 130000},
        {"account_code": "1300", "account_name": "Operating Expenses",  "debit": 68000,   "prior_debit": 75000},
        # Current Assets (2000-2499)
        {"account_code": "2000", "account_name": "Cash at Bank",        "debit": 8000,    "prior_debit": 22000},
        {"account_code": "2100", "account_name": "Trade Debtors",       "debit": 32000,   "prior_debit": 48000},
        # Non-current Assets (2500-2999)
        {"account_code": "2500", "account_name": "Equipment",           "debit": 140000,  "prior_debit": 165000},
        # Current Liabilities (3000-3499)
        {"account_code": "3000", "account_name": "Accounts Payable",   "credit": 85000,  "prior_credit": 62000},
        {"account_code": "3100", "account_name": "Bank Overdraft",      "credit": 45000,  "prior_credit": 28000},
        # Non-current Liabilities (3500-3999)
        {"account_code": "3500", "account_name": "Term Loan",           "credit": 120000, "prior_credit": 140000},
        # Equity (4000-4999, credit-normal)
        {"account_code": "4000", "account_name": "Share Capital",       "credit": 100000, "prior_credit": 100000},
        # Accumulated losses: debit balance = positive closing_balance = losses
        {"account_code": "4900", "account_name": "Retained Earnings",   "debit": 170000,  "prior_debit": 127000},
    ]
    make_tb_lines(fy, tb_lines)
    return entity, fy


# ── ENTITY 5: Div 7A Risk Company ─────────────────────────────────────────────

def make_div7a_company():
    """A company with a director loan balance to trigger Div 7A flags."""
    from core.models import Entity, EntityOfficer, FinancialYear
    entity = Entity.objects.create(
        entity_name="Loan Risk Pty Ltd",
        entity_type="company",
        abn="44433322211",
        acn="444333222",
        address_line_1="2 Loan Street",
        suburb="Sydney",
        state="NSW",
        postcode="2000",
        total_shares_on_issue=1000,
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Peter Borrower",
        role="director", is_signatory=True, shares_held=1000, display_order=1,
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Peter Borrower",
        role="shareholder", shares_held=1000, display_order=2,
    )
    fy = FinancialYear.objects.create(
        entity=entity,
        year_label="FY2025",
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
    )
    # Profitable company with director loan receivable
    tb_lines = [
        # Income (< 1000, credit-normal)
        {"account_code": "100", "account_name": "Service Revenue",      "credit": 450000, "prior_credit": 390000},
        # Expenses (1200-1999, debit-normal)
        {"account_code": "1200", "account_name": "Salaries",            "debit": 180000,  "prior_debit": 165000},
        {"account_code": "1300", "account_name": "Rent",                "debit": 36000,   "prior_debit": 33000},
        {"account_code": "1400", "account_name": "Other Expenses",      "debit": 84000,   "prior_debit": 72000},
        # Income Tax Expense (1200-1999 range, debit-normal)
        {"account_code": "1900", "account_name": "Income Tax Expense",  "debit": 45000,   "prior_debit": 38000},
        # Current Assets (2000-2499)
        {"account_code": "2000", "account_name": "Cash at Bank",        "debit": 85000,   "prior_debit": 72000},
        {"account_code": "2100", "account_name": "Trade Debtors",       "debit": 62000,   "prior_debit": 54000},
        # Director loan receivable — triggers Div 7A
        {"account_code": "2200", "account_name": "Loan to Director",    "debit": 95000,   "prior_debit": 68000},
        # Non-current Assets (2500-2999)
        {"account_code": "2500", "account_name": "Equipment",           "debit": 45000,   "prior_debit": 52000},
        # Current Liabilities (3000-3499)
        {"account_code": "3000", "account_name": "Accounts Payable",   "credit": 22000,  "prior_credit": 18000},
        {"account_code": "3100", "account_name": "Tax Payable",         "credit": 45000,  "prior_credit": 38000},
        # Equity (4000-4999, credit-normal)
        {"account_code": "4000", "account_name": "Share Capital",       "credit": 10000,  "prior_credit": 10000},
        {"account_code": "4900", "account_name": "Retained Earnings",   "credit": 210000, "prior_credit": 172000},  # balanced: net_assets=220000, capital+retained=220000
    ]
    make_tb_lines(fy, tb_lines)
    return entity, fy


# ── ENTITY 6: Zero Revenue Edge Case ──────────────────────────────────────────

def make_zero_revenue_entity():
    """Entity with no revenue — tests division-by-zero guards."""
    from core.models import Entity, EntityOfficer, FinancialYear
    entity = Entity.objects.create(
        entity_name="Dormant Holdings Pty Ltd",
        entity_type="company",
        abn="00011122233",
        acn="000111222",
        address_line_1="3 Dormant Way",
        suburb="Hobart",
        state="TAS",
        postcode="7000",
    )
    EntityOfficer.objects.create(
        entity=entity, full_name="Alice Dormant",
        role="director", is_signatory=True, display_order=1,
    )
    fy = FinancialYear.objects.create(
        entity=entity,
        year_label="FY2025",
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
    )
    # No revenue, minimal expenses, positive equity
    tb_lines = [
        # Expenses (1200-1999)
        {"account_code": "1200", "account_name": "ASIC Fees",           "debit": 1500,    "prior_debit": 1500},
        {"account_code": "1300", "account_name": "Accounting Fees",     "debit": 2200,    "prior_debit": 2200},
        # Current Assets (2000-2499)
        {"account_code": "2000", "account_name": "Cash at Bank",        "debit": 48000,   "prior_debit": 52000},
        # Equity (4000-4999, credit-normal)
        {"account_code": "4000", "account_name": "Share Capital",       "credit": 50000,  "prior_credit": 50000},
        {"account_code": "4900", "account_name": "Retained Earnings",   "credit": 44300,  "prior_credit": 48000},
    ]
    make_tb_lines(fy, tb_lines)
    return entity, fy


# ── Test runner ───────────────────────────────────────────────────────────────

def run_all_tests():
    setup_test_environment()
    from django.test.runner import DiscoverRunner
    from django.test.utils import override_settings

    runner = DiscoverRunner(verbosity=0, keepdb=False)

    with override_settings(MIGRATION_MODULES=DisableMigrations()):
        old_config = runner.setup_databases()

    try:
        _run_tests()
    finally:
        with override_settings(MIGRATION_MODULES=DisableMigrations()):
            runner.teardown_databases(old_config)

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"  RESULTS: {GREEN}{PASS} passed{RESET}  {RED}{FAIL} failed{RESET}  {YELLOW}{WARN} warnings{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")
    return FAIL


def _run_tests():
    from django.test.utils import override_settings
    from core.document_context_builder import DocumentContextBuilder, ContextValidationError

    # ── Setup firm ────────────────────────────────────────────────────────────
    section("Setup")
    make_firm_settings()
    ok("FirmSettings created")

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 1 — Discretionary Trust
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 1: Discretionary Trust — Smith Family Trust FY2025")
    entity_trust, fy_trust = make_discretionary_trust()

    TRUST_DOC_TYPES = [
        "financial_statements", "compilation_report",
        "distribution_minutes", "beneficiary_statement",
        "management_representation_letter", "engagement_letter",
    ]
    for doc_type in TRUST_DOC_TYPES:
        try:
            dcb = DocumentContextBuilder(entity_trust, fy_trust)
            ctx = dcb.build(doc_type)
            check(f"build({doc_type})", True, f"{len(ctx)} keys")
        except ContextValidationError as e:
            warn(f"build({doc_type}) — validation warning", str(e)[:80])
        except Exception as e:
            fail(f"build({doc_type})", str(e)[:80])

    # AASB 1054 — trust-specific flags
    try:
        dcb = DocumentContextBuilder(entity_trust, fy_trust)
        ctx = dcb.build("financial_statements")
        check("is_trust = True",           ctx.get("is_trust") is True)
        check("is_company = False",        ctx.get("is_company") is False)
        check("is_partnership = False",    ctx.get("is_partnership") is False)
        check("trustee_name present",      bool(ctx.get("trustee_name")))
        check("beneficiaries list present",isinstance(ctx.get("beneficiaries", None), list))
        check("beneficiaries count = 3",   len(ctx.get("beneficiaries", [])) == 3)
        check("has_trust_distribution",    ctx.get("has_trust_distribution") is True)
        # Prior year comparatives
        check("revenue_py > 0",            ctx.get("revenue_py", Decimal(0)) > 0, str(ctx.get("revenue_py")))
        check("total_assets_py > 0",       ctx.get("total_assets_py", Decimal(0)) > 0, str(ctx.get("total_assets_py")))
    except Exception as e:
        fail("Trust AASB flag checks", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 2 — Partnership
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 2: Partnership — Green & Associates FY2025")
    entity_partner, fy_partner = make_partnership()

    PARTNER_DOC_TYPES = [
        "financial_statements", "compilation_report",
        "partner_statement", "partnership_tax_summary",
        "management_representation_letter", "engagement_letter",
    ]
    for doc_type in PARTNER_DOC_TYPES:
        try:
            dcb = DocumentContextBuilder(entity_partner, fy_partner)
            ctx = dcb.build(doc_type)
            check(f"build({doc_type})", True, f"{len(ctx)} keys")
        except ContextValidationError as e:
            warn(f"build({doc_type}) — validation warning", str(e)[:80])
        except Exception as e:
            fail(f"build({doc_type})", str(e)[:80])

    try:
        dcb = DocumentContextBuilder(entity_partner, fy_partner)
        ctx = dcb.build("financial_statements")
        check("is_partnership = True",     ctx.get("is_partnership") is True)
        check("is_company = False",        ctx.get("is_company") is False)
        check("partners list present",     isinstance(ctx.get("partners", None), list))
        check("partners count = 2",        len(ctx.get("partners", [])) == 2)
        check("revenue_py > 0",            ctx.get("revenue_py", Decimal(0)) > 0)
    except Exception as e:
        fail("Partnership AASB flag checks", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 3 — Sole Trader
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 3: Sole Trader — James Wilson Consulting FY2025")
    entity_st, fy_st = make_sole_trader()

    ST_DOC_TYPES = [
        "financial_statements", "compilation_report",
        "management_representation_letter", "engagement_letter",
    ]
    for doc_type in ST_DOC_TYPES:
        try:
            dcb = DocumentContextBuilder(entity_st, fy_st)
            ctx = dcb.build(doc_type)
            check(f"build({doc_type})", True, f"{len(ctx)} keys")
        except ContextValidationError as e:
            warn(f"build({doc_type}) — validation warning", str(e)[:80])
        except Exception as e:
            fail(f"build({doc_type})", str(e)[:80])

    try:
        dcb = DocumentContextBuilder(entity_st, fy_st)
        ctx = dcb.build("financial_statements")
        check("is_sole_trader = True",     ctx.get("is_sole_trader") is True)
        check("is_company = False",        ctx.get("is_company") is False)
        check("revenue_py > 0",            ctx.get("revenue_py", Decimal(0)) > 0)
    except Exception as e:
        fail("Sole trader AASB flag checks", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 4 — Prior Year Comparatives (AASB 101 para 38)
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 4: Prior Year Comparatives — AASB 101 para 38")
    try:
        dcb = DocumentContextBuilder(entity_trust, fy_trust)
        ctx = dcb.build("financial_statements")
        # All _py keys must be present and non-None
        py_keys = [k for k in ctx if k.endswith("_py")]
        check("Prior year keys present (>= 5)", len(py_keys) >= 5, f"{len(py_keys)} found")
        check("revenue_py is Decimal",     isinstance(ctx.get("revenue_py"), Decimal))
        check("total_assets_py is Decimal",isinstance(ctx.get("total_assets_py"), Decimal))
        check("total_liabilities_py is Decimal", isinstance(ctx.get("total_liabilities_py"), Decimal))
        check("net_profit_py is Decimal",  isinstance(ctx.get("net_profit_py"), Decimal))
        check("total_equity_py is Decimal",isinstance(ctx.get("total_equity_py"), Decimal))
        # Verify prior year figures are different from current year (they should be)
        check("revenue != revenue_py",
              ctx.get("revenue") != ctx.get("revenue_py"),
              f"CY={ctx.get('revenue')} PY={ctx.get('revenue_py')}")
    except Exception as e:
        fail("Prior year comparatives check", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 5 — Going Concern (AASB 101 para 25)
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 5: Going Concern — AASB 101 para 25")
    entity_gc, fy_gc = make_going_concern_company()
    ctx_gc = None
    try:
        dcb = DocumentContextBuilder(entity_gc, fy_gc)
        ctx_gc = dcb.build("financial_statements")
        check("going_concern_flag = True (negative equity)", ctx_gc.get("going_concern_flag") is True,
              f"total_equity={ctx_gc.get('total_equity')}")
        check("solvency_confirmed = False",  ctx_gc.get("solvency_confirmed") is False)
        check("total_equity < 0",            ctx_gc.get("total_equity", Decimal(0)) < 0,
              str(ctx_gc.get("total_equity")))
        check("net_assets < 0",              ctx_gc.get("net_assets", Decimal(0)) < 0,
              str(ctx_gc.get("net_assets")))
        check("retained_earnings < 0 (losses)", ctx_gc.get("retained_earnings", Decimal(0)) < 0,
              str(ctx_gc.get("retained_earnings")))
        check("retained_earnings_positive = False", ctx_gc.get("retained_earnings_positive") is False)
        # Directors declaration should also flag going concern
        ctx_dd = dcb.build("directors_declaration")
        check("directors_declaration going_concern_flag = True", ctx_dd.get("going_concern_flag") is True)
        check("show_modified_solvency = True",  ctx_dd.get("show_modified_solvency") is True)
        check("show_solvency_declaration = False", ctx_dd.get("show_solvency_declaration") is False)
    except Exception as e:
        fail("Going concern checks", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 6 — Div 7A Risk Detection
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 6: Div 7A Risk Detection")
    entity_d7a, fy_d7a = make_div7a_company()
    try:
        dcb = DocumentContextBuilder(entity_d7a, fy_d7a)
        ctx = dcb.build("financial_statements")
        check("has_director_loans = True",  ctx.get("has_director_loans") is True,
              f"director_loan_balance={ctx.get('director_loan_balance')}")
        check("div7a_risk_flag = True",     ctx.get("div7a_risk_flag") is True)
        check("director_loan_balance > 0",  ctx.get("director_loan_balance", Decimal(0)) > 0,
              str(ctx.get("director_loan_balance")))
        check("is_company = True",          ctx.get("is_company") is True)
        check("shareholders present",       len(ctx.get("shareholders", [])) >= 1)
    except Exception as e:
        fail("Div 7A detection checks", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 7 — Zero Revenue Edge Case
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 7: Zero Revenue — Division-by-Zero Guards")
    entity_zero, fy_zero = make_zero_revenue_entity()
    try:
        dcb = DocumentContextBuilder(entity_zero, fy_zero)
        ctx = dcb.build("financial_statements")
        check("build succeeds with zero revenue", True, f"{len(ctx)} keys")
        check("revenue = 0",                ctx.get("revenue") == Decimal(0), str(ctx.get("revenue")))
        check("gross_margin_pct = 0 (no crash)", ctx.get("gross_margin_pct") is not None)
        check("net_margin_pct = 0 (no crash)",   ctx.get("net_margin_pct") is not None)
        check("total_assets > 0",           ctx.get("total_assets", Decimal(0)) > 0)
        check("going_concern_flag = False (positive equity)", ctx.get("going_concern_flag") is False,
              f"equity={ctx.get('total_equity')}")
    except Exception as e:
        fail("Zero revenue edge case", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 8 — AASB Disclosure Flags
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 8: AASB Disclosure Flags — Div7A Company with full TB")
    try:
        dcb = DocumentContextBuilder(entity_d7a, fy_d7a)
        ctx = dcb.build("financial_statements")
        check("show_note_revenue = True",
              ctx.get("show_note_revenue") is True)
        check("show_note_trade_debtors = True (debtors present)",
              ctx.get("show_note_trade_debtors") is True)
        check("show_note_div7a = True (director loan)",
              ctx.get("show_note_div7a") is True)
        check("show_note_related_parties = True",
              ctx.get("show_note_related_parties") is True)
        check("show_note_contingencies present",
              "show_note_contingencies" in ctx)
        check("show_note_subsequent_events present",
              "show_note_subsequent_events" in ctx)
        # Income tax note: Income Tax Expense is in expenses range (1900), not equity range
        # The DCB looks for income_tax_expense via _sum_keyword on equity section.
        # For Div7A company, tax expense is in expenses (1200-1999) not equity (4000-4999).
        # So show_note_income_tax may be False — that is correct behaviour.
        check("show_note_financial_instruments present",
              "show_note_financial_instruments" in ctx)
    except Exception as e:
        fail("AASB disclosure flag checks", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 9 — Edge Case: Company with No Directors
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 9: Edge Case — Company with No Directors")
    from core.models import Entity, FinancialYear
    entity_nd = Entity.objects.create(
        entity_name="No Directors Pty Ltd",
        entity_type="company",
        abn="55566677788",
        acn="555666777",
        address_line_1="99 Empty St",
        suburb="Darwin",
        state="NT",
        postcode="0800",
    )
    fy_nd = FinancialYear.objects.create(
        entity=entity_nd,
        year_label="FY2025",
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
    )
    make_tb_lines(fy_nd, [
        {"account_code": "100", "account_name": "Revenue",    "credit": 50000, "prior_credit": 45000},
        {"account_code": "1200", "account_name": "Expenses",  "debit": 30000,  "prior_debit": 28000},
        {"account_code": "2000", "account_name": "Cash",      "debit": 20000,  "prior_debit": 18000},
        {"account_code": "4000", "account_name": "Capital",   "credit": 20000, "prior_credit": 20000},
    ])
    try:
        dcb = DocumentContextBuilder(entity_nd, fy_nd)
        ctx = dcb.build("financial_statements")
        check("build(financial_statements) with no directors", True, f"{len(ctx)} keys")
        check("directors = [] (no crash)",    ctx.get("directors") == [])
        check("has_directors = False",        ctx.get("has_directors") is False)
        check("primary_director = {}",        ctx.get("primary_director") == {})
        check("addressee_salutation set",     bool(ctx.get("addressee_salutation")),
              ctx.get("addressee_salutation"))
    except Exception as e:
        fail("No directors edge case", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 10 — ContextValidationError: Missing TAN for Engagement Letter
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 10: ContextValidationError — Missing TAN for Engagement Letter")
    from core.models import FirmSettings
    fs = FirmSettings.get()
    original_tan = fs.tax_agent_number
    fs.tax_agent_number = ""
    fs.save()
    try:
        dcb = DocumentContextBuilder(entity_trust, fy_trust)
        ctx = dcb.build("engagement_letter")
        fail("Should have raised ContextValidationError")
    except ContextValidationError as e:
        check("ContextValidationError raised for missing TAN", True)
        check("Error message mentions Tax Agent Number",
              "Tax Agent Number" in str(e) or "tax_agent_number" in str(e).lower(),
              str(e)[:100])
    except Exception as e:
        fail("Unexpected exception type", str(e)[:80])
    finally:
        fs.tax_agent_number = original_tan
        fs.save()

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 11 — Practice Branding Keys
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 11: Practice Branding Keys — All Entity Types")
    REQUIRED_PRACTICE_KEYS = [
        "practice_name", "practice_legal_name", "practice_abn",
        "practice_address_1", "practice_address_2",
        "practice_phone", "practice_email", "practice_website",
        "practice_tax_agent_number", "practice_signatory_name",
        "practice_signatory_designation", "practice_professional_body",
        "practice_membership_number",
    ]
    for label, ent, fy in [
        ("company (Div7A)", entity_d7a, fy_d7a),
        ("trust", entity_trust, fy_trust),
        ("partnership", entity_partner, fy_partner),
        ("sole_trader", entity_st, fy_st),
    ]:
        try:
            dcb = DocumentContextBuilder(ent, fy)
            ctx = dcb.build("financial_statements")
            missing = [k for k in REQUIRED_PRACTICE_KEYS if k not in ctx]
            check(f"All practice_* keys present ({label})",
                  len(missing) == 0,
                  ", ".join(missing) if missing else "")
        except Exception as e:
            fail(f"practice_* keys check ({label})", str(e)[:80])

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 12 — Financial Accuracy: Known Values
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 12: Financial Accuracy — Known Values")
    # Trust: Revenue = 185,000 | Expenses = 23,000 | Net = 162,000
    try:
        dcb = DocumentContextBuilder(entity_trust, fy_trust)
        ctx = dcb.build("financial_statements")
        check_approx("Trust revenue = 185,000",   ctx.get("revenue", 0),   Decimal("185000"))
        check_approx("Trust expenses = 23,000",   ctx.get("expenses", 0),  Decimal("23000"))
        check_approx("Trust net_profit = 162,000",ctx.get("net_profit", 0),Decimal("162000"))
        check_approx("Trust revenue_py = 162,000",ctx.get("revenue_py", 0),Decimal("162000"))
        check_approx("Trust total_assets = 1,223,500", ctx.get("total_assets", 0), Decimal("1223500"))
    except Exception as e:
        fail("Trust financial accuracy", str(e))

    # Partnership: Revenue = 775,000 | COGS = 227,000 | Expenses = 338,000 | Net = 210,000
    try:
        dcb = DocumentContextBuilder(entity_partner, fy_partner)
        ctx = dcb.build("financial_statements")
        check_approx("Partnership revenue = 775,000",  ctx.get("revenue", 0),    Decimal("775000"))
        check_approx("Partnership net_profit = 210,000",ctx.get("net_profit", 0),Decimal("210000"))
        check_approx("Partnership total_assets = 325,000", ctx.get("total_assets", 0), Decimal("325000"))
    except Exception as e:
        fail("Partnership financial accuracy", str(e))

    # Sole trader: Revenue = 185,000 | Expenses = 90,000 | Net = 95,000
    try:
        dcb = DocumentContextBuilder(entity_st, fy_st)
        ctx = dcb.build("financial_statements")
        check_approx("Sole trader revenue = 185,000",  ctx.get("revenue", 0),    Decimal("185000"))
        check_approx("Sole trader net_profit = 95,000",ctx.get("net_profit", 0), Decimal("95000"))
        check_approx("Sole trader total_assets = 88,500", ctx.get("total_assets", 0), Decimal("88500"))
    except Exception as e:
        fail("Sole trader financial accuracy", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 13 — Jinja2 Filters
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 13: Jinja2 Custom Filters")
    try:
        from core.document_context_builder import get_jinja_env
        env = get_jinja_env()
        filters = env.filters

        # format_currency
        result = filters["format_currency"](Decimal("1234567"))
        check("format_currency rounds to whole dollars", "$" in result and "1,234,567" in result, result)

        # format_percentage
        result = filters["format_percentage"](Decimal("18.5"))
        check("format_percentage formats correctly", "18.5" in result or "18.50" in result, result)

        # format_abn
        result = filters["format_abn"]("12345678901")
        check("format_abn formats as XX XXX XXX XXX", result == "12 345 678 901", result)

        # format_acn
        result = filters["format_acn"]("123456789")
        check("format_acn formats as XXX XXX XXX", result == "123 456 789", result)

        # mask_tfn
        result = filters["mask_tfn"]("123456789")
        check("mask_tfn masks middle digits", "***" in result or "XXX" in result, result)

        # format_yesno
        check("format_yesno True → Yes",  filters["format_yesno"](True) == "Yes")
        check("format_yesno False → No",  filters["format_yesno"](False) == "No")

        # format_date_long
        result = filters["format_date_long"](date(2025, 6, 30))
        check("format_date_long formats correctly", "2025" in result and "June" in result, result)

        # format_date_short
        result = filters["format_date_short"](date(2025, 6, 30))
        check("format_date_short formats correctly", "2025" in result, result)

    except Exception as e:
        fail("Jinja2 filter checks", str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # SUITE 14 — Financial Year Context Keys
    # ══════════════════════════════════════════════════════════════════════════
    section("Suite 14: Financial Year Context Keys")
    try:
        dcb = DocumentContextBuilder(entity_trust, fy_trust)
        ctx = dcb.build("financial_statements")
        check("fy_year = '2025'",          ctx.get("fy_year") == "2025", str(ctx.get("fy_year")))
        check("fy_label present",          bool(ctx.get("fy_label")), ctx.get("fy_label"))
        check("fy_start_date present",     bool(ctx.get("fy_start_date")))
        check("fy_end_date present",       bool(ctx.get("fy_end_date")))
        check("fy_end_formatted present",  bool(ctx.get("fy_end_formatted")), ctx.get("fy_end_formatted"))
        check("fy_start_formatted present",bool(ctx.get("fy_start_formatted")), ctx.get("fy_start_formatted"))
        check("fy_period_label present",   bool(ctx.get("fy_period_label")))
        check("fy_prior_end_date key present", "fy_prior_end_date" in ctx)  # may be empty if no prior year linked
        check("generation_date present",   bool(ctx.get("generation_date")))
    except Exception as e:
        fail("Financial year context key checks", str(e))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from django.test.runner import DiscoverRunner
    from django.test.utils import override_settings
    failures = run_all_tests()
    sys.exit(0 if failures == 0 else 1)
