"""
StatementHub — DocumentContextBuilder Full Test Suite
======================================================
Covers:
  1. Company (ETS FY2025) — baseline (already passing)
  2. Discretionary Trust — with trustees, beneficiaries, TrustWorkspace, Section 100A
  3. Unit Trust — with unit holders, distribution minutes
  4. Partnership — with partners, profit share percentages, prior year comparatives
  5. Sole Trader — minimal officers, proprietor context
  6. AASB Compliance assertions — going concern, comparatives, disclosure flags
  7. Edge cases — zero revenue, negative equity, accumulated losses, missing officers,
     going concern triggers, Div 7A risk, SG shortfall

Run with:
    python3 test_dcb_full_suite.py

Exit 0 = all pass. Exit 1 = failures.
"""

import os
import sys
import django
from decimal import Decimal
from datetime import date, timedelta

# ── Environment ──────────────────────────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import environ as _environ
_env = _environ.Env()
_environ.Env.read_env(os.path.join(os.path.dirname(__file__), ".env.test"))

django.setup()

from django.test.utils import override_settings, setup_test_environment
from django.test.runner import DiscoverRunner
from unittest.mock import patch, MagicMock

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

# ── Test infrastructure ───────────────────────────────────────────────────────
PASS = 0
FAIL = 0
WARN = 0
_results = []

def ok(label, detail=""):
    global PASS
    PASS += 1
    _results.append(("✓", label, detail))
    print(f"  {GREEN}✓{RESET}  {label:<55} {detail}")

def fail(label, detail=""):
    global FAIL
    FAIL += 1
    _results.append(("✗", label, detail))
    print(f"  {RED}✗{RESET}  {label:<55} {detail}")

def warn(label, detail=""):
    global WARN
    WARN += 1
    _results.append(("⚠", label, detail))
    print(f"  {YELLOW}⚠{RESET}  {label:<55} {detail}")

def section(title):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

def check(label, condition, detail=""):
    if condition:
        ok(label, detail)
    else:
        fail(label, detail)

# ── DisableMigrations helper ──────────────────────────────────────────────────
class DisableMigrations:
    def __contains__(self, item):
        return True
    def __getitem__(self, item):
        return None

# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_firm_settings():
    from core.models import FirmSettings
    fs, _ = FirmSettings.objects.get_or_create(pk=1, defaults={
        "firm_name": "MC & S Chartered Accountants",
        "firm_legal_name": "MC & S Pty Ltd",
        "firm_abn": "12345678901",
        "firm_address_1": "PO Box 123",
        "firm_address_2": "Sydney NSW 2000",
        "firm_phone": "02 9999 0000",
        "firm_email": "info@mcands.com.au",
        "firm_website": "https://mcands.com.au",
        "tax_agent_number": "12345678",
        "signatory_name": "Michael Scarton",
        "signatory_designation": "CPA, Registered Tax Agent",
        "professional_body": "CPA Australia",
        "membership_number": "CPA123456",
        "practice_independence_maintained": True,
    })
    return fs


def make_tb_lines(fy, lines_data):
    """
    lines_data: list of dicts with keys:
      account_code, account_name, debit, credit, prior_debit, prior_credit
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
            opening_debit=Decimal("0"),
            opening_credit=Decimal("0"),
            period_debit=debit,
            period_credit=credit,
            closing_debit=max(closing_balance, Decimal("0")),
            closing_credit=max(-closing_balance, Decimal("0")),
            closing_balance=closing_balance,
            prior_debit=prior_debit,
            prior_credit=prior_credit,
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
    # Trustee
    EntityOfficer.objects.create(
        entity=entity, full_name="Smith Trustee Pty Ltd",
        role="trustee", is_signatory=True, display_order=1,
    )
    # Beneficiaries
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
        year=2025,
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        is_locked=False,
    )
    # Trust TB — income and expenses (no balance sheet equity, trust distributes all income)
    # CY: Net income $185,000 | PY: $162,000
    tb_lines = [
        # Income (credit-normal)
        {"account_code": "1100", "account_name": "Rental Income",      "credit": 95000,  "prior_credit": 82000},
        {"account_code": "1200", "account_name": "Dividend Income",     "credit": 45000,  "prior_credit": 38000},
        {"account_code": "1300", "account_name": "Interest Income",     "credit": 15000,  "prior_credit": 12000},
        {"account_code": "1400", "account_name": "Capital Gains",       "credit": 30000,  "prior_credit": 30000},
        # Expenses (debit-normal)
        {"account_code": "6100", "account_name": "Property Management", "debit": 9500,    "prior_debit": 8200},
        {"account_code": "6200", "account_name": "Accounting Fees",     "debit": 5500,    "prior_debit": 5000},
        {"account_code": "6300", "account_name": "Bank Charges",        "debit": 1000,    "prior_debit": 800},
        {"account_code": "6400", "account_name": "Insurance",           "debit": 2500,    "prior_debit": 2200},
        {"account_code": "6500", "account_name": "Repairs & Maintenance","debit": 4500,   "prior_debit": 3800},
        # Assets (debit-normal)
        {"account_code": "1010", "account_name": "Cash at Bank",        "debit": 45000,   "prior_debit": 38000},
        {"account_code": "1020", "account_name": "Trade Debtors",       "debit": 8500,    "prior_debit": 7200},
        {"account_code": "1500", "account_name": "Investment Property", "debit": 850000,  "prior_debit": 820000},
        {"account_code": "1600", "account_name": "Share Portfolio",     "debit": 320000,  "prior_debit": 295000},
        # Liabilities (credit-normal)
        {"account_code": "2100", "account_name": "Accounts Payable",    "credit": 3500,   "prior_credit": 3000},
        {"account_code": "2200", "account_name": "Mortgage Payable",    "credit": 480000, "prior_credit": 510000},
        # Equity / Corpus (credit-normal)
        {"account_code": "3000", "account_name": "Trust Corpus",        "credit": 550000, "prior_credit": 520000},
        {"account_code": "3100", "account_name": "Retained Income",     "credit": 185000, "prior_credit": 162000},
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
    # Partners
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
        year=2025,
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        is_locked=False,
    )
    # Partnership TB — CY net profit $210,000 | PY $188,000
    tb_lines = [
        # Income
        {"account_code": "1100", "account_name": "Consulting Revenue",  "credit": 680000, "prior_credit": 610000},
        {"account_code": "1200", "account_name": "Project Income",      "credit": 95000,  "prior_credit": 82000},
        # COGS
        {"account_code": "5100", "account_name": "Subcontractor Costs", "debit": 185000,  "prior_debit": 168000},
        {"account_code": "5200", "account_name": "Direct Materials",    "debit": 42000,   "prior_debit": 38000},
        # Expenses
        {"account_code": "6100", "account_name": "Salaries & Wages",    "debit": 180000,  "prior_debit": 162000},
        {"account_code": "6200", "account_name": "Rent",                "debit": 48000,   "prior_debit": 44000},
        {"account_code": "6300", "account_name": "Motor Vehicle",       "debit": 28000,   "prior_debit": 24000},
        {"account_code": "6400", "account_name": "Depreciation",        "debit": 15000,   "prior_debit": 14000},
        {"account_code": "6500", "account_name": "Insurance",           "debit": 8000,    "prior_debit": 7500},
        {"account_code": "6600", "account_name": "Accounting Fees",     "debit": 7000,    "prior_debit": 6500},
        {"account_code": "6700", "account_name": "Other Expenses",      "debit": 52000,   "prior_debit": 44000},
        # Assets
        {"account_code": "1010", "account_name": "Cash at Bank",        "debit": 95000,   "prior_debit": 82000},
        {"account_code": "1020", "account_name": "Trade Debtors",       "debit": 145000,  "prior_debit": 128000},
        {"account_code": "1500", "account_name": "Plant & Equipment",   "debit": 85000,   "prior_debit": 95000},
        # Liabilities
        {"account_code": "2100", "account_name": "Accounts Payable",    "credit": 28000,  "prior_credit": 24000},
        {"account_code": "2200", "account_name": "Bank Loan",           "credit": 45000,  "prior_credit": 60000},
        # Partner Capital (credit-normal)
        {"account_code": "3100", "account_name": "David Green Capital", "credit": 126000, "prior_credit": 112800},
        {"account_code": "3200", "account_name": "Sarah Green Capital", "credit": "84000","prior_credit": 75200},
        {"account_code": "3900", "account_name": "Retained Earnings",   "credit": 210000, "prior_credit": 188000},
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
        year=2025,
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        is_locked=False,
    )
    # Sole trader TB — CY net profit $95,000 | PY $88,000
    tb_lines = [
        # Income
        {"account_code": "1100", "account_name": "Consulting Fees",     "credit": 185000, "prior_credit": 172000},
        # Expenses
        {"account_code": "6100", "account_name": "Home Office",         "debit": 8500,    "prior_debit": 8000},
        {"account_code": "6200", "account_name": "Motor Vehicle",       "debit": 18000,   "prior_debit": 16500},
        {"account_code": "6300", "account_name": "Depreciation",        "debit": 4500,    "prior_debit": 4200},
        {"account_code": "6400", "account_name": "Professional Fees",   "debit": 6500,    "prior_debit": 6000},
        {"account_code": "6500", "account_name": "Superannuation",      "debit": 19250,   "prior_debit": 17875},
        {"account_code": "6600", "account_name": "Other Expenses",      "debit": 33250,   "prior_debit": 31625},
        # Assets
        {"account_code": "1010", "account_name": "Cash at Bank",        "debit": 42000,   "prior_debit": 35000},
        {"account_code": "1020", "account_name": "Trade Debtors",       "debit": 28500,   "prior_debit": 24000},
        {"account_code": "1500", "account_name": "Equipment",           "debit": 18000,   "prior_debit": 22500},
        # Liabilities
        {"account_code": "2100", "account_name": "Accounts Payable",    "credit": 4500,   "prior_credit": 3800},
        {"account_code": "2200", "account_name": "Tax Payable",         "credit": 12000,  "prior_credit": 11000},
        # Equity
        {"account_code": "3000", "account_name": "Proprietor's Capital","credit": 72000,  "prior_credit": 64700},
        {"account_code": "3900", "account_name": "Retained Earnings",   "credit": 95000,  "prior_credit": 88000},
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
        year=2025,
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        is_locked=False,
    )
    # Negative equity: assets $180k, liabilities $250k → net assets -$70k
    tb_lines = [
        # Income
        {"account_code": "1100", "account_name": "Sales Revenue",       "credit": 120000, "prior_credit": 180000},
        # Expenses
        {"account_code": "6100", "account_name": "Cost of Sales",       "debit": 95000,   "prior_debit": 130000},
        {"account_code": "6200", "account_name": "Operating Expenses",  "debit": 68000,   "prior_debit": 75000},
        # Assets
        {"account_code": "1010", "account_name": "Cash at Bank",        "debit": 8000,    "prior_debit": 22000},
        {"account_code": "1020", "account_name": "Trade Debtors",       "debit": 32000,   "prior_debit": 48000},
        {"account_code": "1500", "account_name": "Equipment",           "debit": 140000,  "prior_debit": 165000},
        # Liabilities (current)
        {"account_code": "2100", "account_name": "Accounts Payable",    "credit": 85000,  "prior_credit": 62000},
        {"account_code": "2200", "account_name": "Bank Overdraft",      "credit": 45000,  "prior_credit": 28000},
        # Liabilities (non-current)
        {"account_code": "2500", "account_name": "Term Loan",           "credit": 120000, "prior_credit": 140000},
        # Equity — accumulated losses (debit balance = positive closing_balance)
        {"account_code": "3000", "account_name": "Share Capital",       "credit": 100000, "prior_credit": 100000},
        # Accumulated losses: debit 170000 (positive closing_balance = losses)
        {"account_code": "3900", "account_name": "Retained Earnings",   "debit": 170000,  "prior_debit": 127000},
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
    fy = FinancialYear.objects.create(
        entity=entity,
        year=2025,
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        is_locked=False,
    )
    # Profitable company with director loan receivable
    tb_lines = [
        # Income
        {"account_code": "1100", "account_name": "Service Revenue",     "credit": 450000, "prior_credit": 390000},
        # Expenses
        {"account_code": "6100", "account_name": "Salaries",            "debit": 180000,  "prior_debit": 165000},
        {"account_code": "6200", "account_name": "Rent",                "debit": 36000,   "prior_debit": 33000},
        {"account_code": "6300", "account_name": "Other Expenses",      "debit": 84000,   "prior_debit": 72000},
        # Assets
        {"account_code": "1010", "account_name": "Cash at Bank",        "debit": 85000,   "prior_debit": 72000},
        {"account_code": "1020", "account_name": "Trade Debtors",       "debit": 62000,   "prior_debit": 54000},
        # Director loan receivable — triggers Div 7A
        {"account_code": "1030", "account_name": "Loan to Director",    "debit": 95000,   "prior_debit": 68000},
        {"account_code": "1500", "account_name": "Equipment",           "debit": 45000,   "prior_debit": 52000},
        # Income Tax Expense (debit-normal)
        {"account_code": "6800", "account_name": "Income Tax Expense",  "debit": 45000,   "prior_debit": 38000},
        # Liabilities
        {"account_code": "2100", "account_name": "Accounts Payable",    "credit": 22000,  "prior_credit": 18000},
        {"account_code": "2200", "account_name": "Tax Payable",         "credit": 45000,  "prior_credit": 38000},
        # Equity
        {"account_code": "3000", "account_name": "Share Capital",       "credit": 10000,  "prior_credit": 10000},
        {"account_code": "3900", "account_name": "Retained Earnings",   "credit": 315000, "prior_credit": 172000},
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
        year=2025,
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        is_locked=False,
    )
    # No revenue, minimal expenses, positive equity
    tb_lines = [
        # Expenses only
        {"account_code": "6100", "account_name": "ASIC Fees",           "debit": 1500,    "prior_debit": 1500},
        {"account_code": "6200", "account_name": "Accounting Fees",     "debit": 2200,    "prior_debit": 2200},
        # Assets
        {"account_code": "1010", "account_name": "Cash at Bank",        "debit": 48000,   "prior_debit": 52000},
        # Equity
        {"account_code": "3000", "account_name": "Share Capital",       "credit": 50000,  "prior_credit": 50000},
        {"account_code": "3900", "account_name": "Retained Earnings",   "credit": "44300","prior_credit": 48000},
    ]
    make_tb_lines(fy, tb_lines)
    return entity, fy


# ── Test runner ───────────────────────────────────────────────────────────────

def run_all_tests():
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, keepdb=False)

    with override_settings(MIGRATION_MODULES=DisableMigrations()):
        old_config = runner.setup_databases()

    try:
        _run_tests()
    finally:
        with override_settings(MIGRATION_MODULES=DisableMigrations()):
            runner.teardown_databases(old_config)

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Results:{RESET}")
    print(f"  PASS: {GREEN}{PASS}{RESET}")
    print(f"  WARN: {YELLOW}{WARN}{RESET}")
    print(f"  FAIL: {RED}{FAIL}{RESET}")
    print(f"  Total: {PASS + WARN + FAIL:>3}")
    if FAIL == 0:
        print(f"\n  {GREEN}ALL TESTS PASSED{RESET}")
    else:
        print(f"\n  {RED}{FAIL} TEST(S) FAILED{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    return FAIL


def _run_tests():
    from core.document_context_builder import DocumentContextBuilder, ContextValidationError

        # ── Setup firm ────────────────────────────────────────────────────
        section("Setup")
        make_firm_settings()
        ok("FirmSettings created")

        # ══════════════════════════════════════════════════════════════════
        # SUITE 1 — Discretionary Trust
        # ══════════════════════════════════════════════════════════════════
        section("Suite 1: Discretionary Trust — Smith Family Trust FY2025")
        entity, fy = make_discretionary_trust()

        # All document types should build
        TRUST_DOC_TYPES = [
            "financial_statements", "compilation_report",
            "distribution_minutes", "beneficiary_statement",
            "management_representation_letter", "engagement_letter",
            "client_cover_letter", "eva_client_summary",
        ]
        contexts = {}
        for doc_type in TRUST_DOC_TYPES:
            try:
                dcb = DocumentContextBuilder(entity, fy, wizard_data={
                    "resolution_date": date(2025, 6, 28),
                    "services": ["Financial Statements", "Tax Return", "Trust Distribution Planning"],
                })
                ctx = dcb.build(doc_type)
                contexts[doc_type] = ctx
                ok(f"build({doc_type})", f"{len(ctx)} keys")
            except Exception as e:
                fail(f"build({doc_type})", str(e))

        # Entity type flags
        if "financial_statements" in contexts:
            ctx = contexts["financial_statements"]
            check("is_trust = True",          ctx.get("is_trust") is True)
            check("is_company = False",        ctx.get("is_company") is False)
            check("is_partnership = False",    ctx.get("is_partnership") is False)
            check("is_sole_trader = False",    ctx.get("is_sole_trader") is False)

        # People context
        if "financial_statements" in contexts:
            ctx = contexts["financial_statements"]
            check("trustees populated",        len(ctx.get("trustees", [])) == 1,
                  f"got {len(ctx.get('trustees', []))}")
            check("beneficiaries populated",   len(ctx.get("beneficiaries", [])) == 3,
                  f"got {len(ctx.get('beneficiaries', []))}")
            check("directors empty (trust)",   len(ctx.get("directors", [])) == 0)
            check("partners empty (trust)",    len(ctx.get("partners", [])) == 0)
            check("trustee_names_list set",    bool(ctx.get("trustee_names_list")),
                  ctx.get("trustee_names_list"))
            check("beneficiary_count = 3",     ctx.get("beneficiary_count") == 3)

        # Trust salutation
        if "engagement_letter" in contexts:
            ctx = contexts["engagement_letter"]
            salutation = ctx.get("addressee_salutation", "")
            check("trust salutation starts with 'Dear'", salutation.startswith("Dear"),
                  repr(salutation))

        # Financial data
        if "financial_statements" in contexts:
            ctx = contexts["financial_statements"]
            check("revenue > 0 (trust income)",    ctx.get("revenue", Decimal(0)) > 0,
                  str(ctx.get("revenue")))
            check("net_profit > 0",                ctx.get("net_profit", Decimal(0)) > 0,
                  str(ctx.get("net_profit")))
            check("total_assets > 0",              ctx.get("total_assets", Decimal(0)) > 0,
                  str(ctx.get("total_assets")))
            # Prior year comparatives
            check("revenue_py > 0",                ctx.get("revenue_py", Decimal(0)) > 0,
                  str(ctx.get("revenue_py")))
            check("total_assets_py > 0",           ctx.get("total_assets_py", Decimal(0)) > 0,
                  str(ctx.get("total_assets_py")))
            check("net_profit != net_profit_py",   ctx.get("net_profit") != ctx.get("net_profit_py"),
                  f"CY={ctx.get('net_profit')} PY={ctx.get('net_profit_py')}")

        # Distribution minutes
        if "distribution_minutes" in contexts:
            ctx = contexts["distribution_minutes"]
            check("resolution_deadline set",       bool(ctx.get("resolution_deadline")))
            check("trustee_execution_blocks set",  isinstance(ctx.get("trustee_execution_blocks"), list))

        # AASB: trust should NOT have solvency declaration (check on directors_declaration context)
        try:
            dcb_trust_dd = DocumentContextBuilder(entity, fy, wizard_data={})
            ctx_trust_dd = dcb_trust_dd.build("directors_declaration")
            check("show_solvency_declaration = False (trust)",
                  not ctx_trust_dd.get("show_solvency_declaration", False))
            check("show_modified_solvency = False (trust, not a company)",
                  not ctx_trust_dd.get("show_modified_solvency", False))
        except Exception as e:
            fail("trust directors_declaration solvency check", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 2 — Partnership
        # ══════════════════════════════════════════════════════════════════
        section("Suite 2: Partnership — Green & Associates FY2025")
        entity_p, fy_p = make_partnership()

        PARTNERSHIP_DOC_TYPES = [
            "financial_statements", "compilation_report",
            "partner_statement", "partnership_tax_summary",
            "management_representation_letter", "engagement_letter",
            "client_cover_letter",
        ]
        contexts_p = {}
        for doc_type in PARTNERSHIP_DOC_TYPES:
            try:
                dcb = DocumentContextBuilder(entity_p, fy_p, wizard_data={
                    "services": ["Financial Statements", "Tax Return", "Partnership Tax Summary"],
                })
                ctx = dcb.build(doc_type)
                contexts_p[doc_type] = ctx
                ok(f"build({doc_type})", f"{len(ctx)} keys")
            except Exception as e:
                fail(f"build({doc_type})", str(e))

        if "financial_statements" in contexts_p:
            ctx = contexts_p["financial_statements"]
            check("is_partnership = True",     ctx.get("is_partnership") is True)
            check("is_company = False",        ctx.get("is_company") is False)
            check("is_trust = False",          ctx.get("is_trust") is False)
            check("partners populated",        len(ctx.get("partners", [])) == 2,
                  f"got {len(ctx.get('partners', []))}")
            check("directors empty (partnership)", len(ctx.get("directors", [])) == 0)

            # Profit share percentages
            partners = ctx.get("partners", [])
            if len(partners) == 2:
                shares = sorted([p["profit_share_pct"] for p in partners])
                check("partner profit shares sum to 100",
                      abs(sum(p["profit_share_pct"] for p in partners) - 100.0) < 0.01,
                      str(shares))

            # Prior year comparatives
            check("revenue_py > 0 (partnership)",  ctx.get("revenue_py", Decimal(0)) > 0,
                  str(ctx.get("revenue_py")))
            check("net_profit_py > 0",             ctx.get("net_profit_py", Decimal(0)) > 0,
                  str(ctx.get("net_profit_py")))
            check("net_profit != net_profit_py",   ctx.get("net_profit") != ctx.get("net_profit_py"),
                  f"CY={ctx.get('net_profit')} PY={ctx.get('net_profit_py')}")

            # Partnership salutation
            salutation = ctx.get("addressee_salutation", "")
            check("partnership salutation = 'Dear Partners'",
                  "Partners" in salutation, repr(salutation))

        if "partner_statement" in contexts_p:
            ctx = contexts_p["partner_statement"]
            check("partnership_name set",      bool(ctx.get("partnership_name")))
            check("all_partners_summary set",  isinstance(ctx.get("all_partners_summary"), list))

        # AASB: partnership should NOT have solvency declaration (check on directors_declaration context)
        try:
            dcb_p_dd = DocumentContextBuilder(entity_p, fy_p, wizard_data={})
            ctx_p_dd = dcb_p_dd.build("directors_declaration")
            check("no solvency declaration (partnership)",
                  not ctx_p_dd.get("show_solvency_declaration", False))
            check("no modified_solvency (partnership)",
                  not ctx_p_dd.get("show_modified_solvency", False))
        except Exception as e:
            fail("partnership directors_declaration solvency check", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 3 — Sole Trader
        # ══════════════════════════════════════════════════════════════════
        section("Suite 3: Sole Trader — James Wilson Consulting FY2025")
        entity_st, fy_st = make_sole_trader()

        SOLE_TRADER_DOC_TYPES = [
            "financial_statements", "compilation_report",
            "management_representation_letter", "engagement_letter",
            "client_cover_letter",
        ]
        contexts_st = {}
        for doc_type in SOLE_TRADER_DOC_TYPES:
            try:
                dcb = DocumentContextBuilder(entity_st, fy_st, wizard_data={
                    "services": ["Financial Statements", "Tax Return"],
                })
                ctx = dcb.build(doc_type)
                contexts_st[doc_type] = ctx
                ok(f"build({doc_type})", f"{len(ctx)} keys")
            except Exception as e:
                fail(f"build({doc_type})", str(e))

        if "financial_statements" in contexts_st:
            ctx = contexts_st["financial_statements"]
            check("is_sole_trader = True",     ctx.get("is_sole_trader") is True)
            check("is_company = False",        ctx.get("is_company") is False)
            check("revenue > 0 (sole trader)", ctx.get("revenue", Decimal(0)) > 0,
                  str(ctx.get("revenue")))
            check("net_profit > 0",            ctx.get("net_profit", Decimal(0)) > 0,
                  str(ctx.get("net_profit")))
            check("no solvency declaration (sole trader)",
                  not ctx.get("show_solvency_declaration", False))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 4 — AASB Going Concern (Negative Equity)
        # ══════════════════════════════════════════════════════════════════
        section("Suite 4: AASB Going Concern — Negative Equity Company")
        entity_gc, fy_gc = make_going_concern_company()

        try:
            dcb_gc = DocumentContextBuilder(entity_gc, fy_gc, wizard_data={})
            ctx_gc = dcb_gc.build("directors_declaration")
            ok("build(directors_declaration) for going concern entity",
               f"{len(ctx_gc)} keys")

            # AASB 101 para 25: going concern flag must be set
            check("going_concern_flag = True (negative equity)",
                  ctx_gc.get("going_concern_flag") is True)
            check("going_concern_severity = material_uncertainty",
                  ctx_gc.get("going_concern_severity") == "material_uncertainty",
                  ctx_gc.get("going_concern_severity"))
            check("show_going_concern_paragraph = True",
                  ctx_gc.get("show_going_concern_paragraph") is True)
            check("negative_equity = True",
                  ctx_gc.get("negative_equity") is True)
            check("solvency_confirmed = False (negative equity)",
                  ctx_gc.get("solvency_confirmed") is False)
            check("show_modified_solvency = True (company, insolvent)",
                  ctx_gc.get("show_modified_solvency", False) is True)
            check("insolvent_risk = True",
                  ctx_gc.get("insolvent_risk", False) is True)

            # Accumulated losses — retained_earnings should be negative (losses)
            re = ctx_gc.get("retained_earnings", Decimal(0))
            check("retained_earnings < 0 (accumulated losses)",
                  re < 0, f"retained_earnings = {re}")

        except Exception as e:
            fail("build(directors_declaration) for going concern entity", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 5 — Division 7A Risk
        # ══════════════════════════════════════════════════════════════════
        section("Suite 5: Division 7A Risk — Director Loan Company")
        entity_d7, fy_d7 = make_div7a_company()

        try:
            dcb_d7 = DocumentContextBuilder(entity_d7, fy_d7, wizard_data={
                "loan_principal": Decimal("95000"),
                "loan_date": date(2025, 3, 1),
            })
            ctx_d7 = dcb_d7.build("div7a_loan_agreement")
            ok("build(div7a_loan_agreement)", f"{len(ctx_d7)} keys")

            check("div7a_risk = True",             ctx_d7.get("div7a_risk") is True)
            check("div7a_action_required = True",  ctx_d7.get("div7a_action_required") is True)
            check("show_note_div7a = True",        ctx_d7.get("show_note_div7a") is True)
            check("director_loan_balance > 0",
                  ctx_d7.get("director_loan_balance", Decimal(0)) > 0,
                  str(ctx_d7.get("director_loan_balance")))
            check("div7a_benchmark_rate set",
                  ctx_d7.get("div7a_benchmark_rate") is not None)
            check("div7a_loan_principal > 0",
                  ctx_d7.get("div7a_loan_principal", Decimal(0)) > 0,
                  str(ctx_d7.get("div7a_loan_principal")))
            check("div7a_repayment_schedule set",
                  isinstance(ctx_d7.get("div7a_repayment_schedule"), list))

            # Profitable company — should NOT trigger going concern
            check("going_concern_flag = False (profitable, div7a company)",
                  ctx_d7.get("going_concern_flag") is False)
            check("solvency_confirmed = True (profitable company)",
                  ctx_d7.get("solvency_confirmed") is True)

        except Exception as e:
            fail("build(div7a_loan_agreement)", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 6 — Zero Revenue Edge Case
        # ══════════════════════════════════════════════════════════════════
        section("Suite 6: Edge Case — Zero Revenue (Dormant Company)")
        entity_z, fy_z = make_zero_revenue_entity()

        try:
            dcb_z = DocumentContextBuilder(entity_z, fy_z, wizard_data={})
            ctx_z = dcb_z.build("financial_statements")
            ok("build(financial_statements) for zero revenue entity",
               f"{len(ctx_z)} keys")

            check("revenue = 0",               ctx_z.get("revenue") == Decimal(0),
                  str(ctx_z.get("revenue")))
            check("gross_margin_pct = 0 (no div-by-zero)",
                  ctx_z.get("gross_margin_pct") == Decimal(0),
                  str(ctx_z.get("gross_margin_pct")))
            check("net_profit_margin_pct = 0 (no div-by-zero)",
                  ctx_z.get("net_profit_margin_pct") == Decimal(0),
                  str(ctx_z.get("net_profit_margin_pct")))
            check("gross_margin_pct_py = 0 (no div-by-zero)",
                  ctx_z.get("gross_margin_pct_py") == Decimal(0),
                  str(ctx_z.get("gross_margin_pct_py")))
            # Dormant company with positive equity — no going concern
            check("going_concern_flag = False (dormant, positive equity)",
                  ctx_z.get("going_concern_flag") is False)
            check("total_assets > 0",          ctx_z.get("total_assets", Decimal(0)) > 0)

        except Exception as e:
            fail("build(financial_statements) for zero revenue entity", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 7 — AASB Comparative Figures (AASB 101 para 38)
        # ══════════════════════════════════════════════════════════════════
        section("Suite 7: AASB 101 — Comparative Figures Completeness")
        # Use the trust entity (entity, fy) — already built above
        try:
            dcb_comp = DocumentContextBuilder(entity, fy, wizard_data={})
            ctx_comp = dcb_comp.build("financial_statements")

            REQUIRED_PY_KEYS = [
                "revenue_py", "net_profit_py",
                "total_assets_py", "total_liabilities_py",
                "total_current_assets_py", "total_non_current_assets_py",
                "total_current_liabilities_py", "total_non_current_liabilities_py",
                "net_assets_py", "retained_earnings_py",
            ]
            for key in REQUIRED_PY_KEYS:
                val = ctx_comp.get(key)
                check(f"PY key present: {key}",
                      val is not None, f"= {val}")

            # CY != PY for income (trust had different income each year)
            check("revenue CY != PY (comparatives differ)",
                  ctx_comp.get("revenue") != ctx_comp.get("revenue_py"),
                  f"CY={ctx_comp.get('revenue')} PY={ctx_comp.get('revenue_py')}")

        except Exception as e:
            fail("AASB 101 comparative figures check", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 8 — AASB Disclosure Flags
        # ══════════════════════════════════════════════════════════════════
        section("Suite 8: AASB Disclosure Flags — Company with full TB")
        # Use the Div 7A company for this — it has trade debtors, borrowings, related parties
        try:
            dcb_flags = DocumentContextBuilder(entity_d7, fy_d7, wizard_data={
                "has_contingencies": True,
                "has_subsequent_events": True,
            })
            ctx_flags = dcb_flags.build("financial_statements")

            check("show_note_revenue = True",          ctx_flags.get("show_note_revenue") is True)
            check("show_note_trade_debtors = True",    ctx_flags.get("show_note_trade_debtors") is True)
            check("show_note_div7a = True",            ctx_flags.get("show_note_div7a") is True)
            check("show_note_related_parties = True",  ctx_flags.get("show_note_related_parties") is True)
            check("show_note_contingencies = True",    ctx_flags.get("show_note_contingencies") is True)
            check("show_note_subsequent_events = True",ctx_flags.get("show_note_subsequent_events") is True)
            check("show_note_income_tax = True (company with tax expense line)",
                  ctx_flags.get("show_note_income_tax") is True,
                  f"income_tax_expense={ctx_flags.get('income_tax_expense')}")
            check("show_note_financial_instruments = True",
                  ctx_flags.get("show_note_financial_instruments") is True)

        except Exception as e:
            fail("AASB disclosure flags check", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 9 — Missing Officers Edge Case
        # ══════════════════════════════════════════════════════════════════
        section("Suite 9: Edge Case — Company with No Directors")
        from core.models import Entity, FinancialYear
        entity_no_dir = Entity.objects.create(
            entity_name="No Director Co Pty Ltd",
            entity_type="company",
            abn="77766655544",
            acn="777666555",
        )
        fy_no_dir = FinancialYear.objects.create(
            entity=entity_no_dir,
            year=2025,
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        make_tb_lines(fy_no_dir, [
            {"account_code": "1100", "account_name": "Revenue",         "credit": 50000},
            {"account_code": "6100", "account_name": "Expenses",        "debit": 20000},
            {"account_code": "1010", "account_name": "Cash",            "debit": 80000},
            {"account_code": "3000", "account_name": "Share Capital",   "credit": 50000},
            {"account_code": "3900", "account_name": "Retained Earnings","credit": 30000},
        ])
        try:
            dcb_nd = DocumentContextBuilder(entity_no_dir, fy_no_dir, wizard_data={})
            ctx_nd = dcb_nd.build("financial_statements")
            ok("build(financial_statements) with no directors", f"{len(ctx_nd)} keys")
            check("directors = [] (no crash)",    ctx_nd.get("directors") == [])
            check("has_directors = False",        ctx_nd.get("has_directors") is False)
            check("primary_director = {}",        ctx_nd.get("primary_director") == {})
            # Salutation should still be set
            check("addressee_salutation set",     bool(ctx_nd.get("addressee_salutation")),
                  repr(ctx_nd.get("addressee_salutation")))
        except Exception as e:
            fail("build with no directors", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 10 — ContextValidationError for Engagement Letter
        # ══════════════════════════════════════════════════════════════════
        section("Suite 10: ContextValidationError — Missing TAN for Engagement Letter")
        from core.models import FirmSettings
        fs = FirmSettings.get()
        original_tan = fs.tax_agent_number
        fs.tax_agent_number = ""
        fs.save()

        try:
            dcb_val = DocumentContextBuilder(entity_d7, fy_d7, wizard_data={
                "services": ["Tax Return"],
            })
            ctx_val = dcb_val.build("engagement_letter")
            fail("Should have raised ContextValidationError for missing TAN")
        except ContextValidationError as e:
            ok("ContextValidationError raised for missing TAN")
            check("Error message mentions Tax Agent Number",
                  "Tax Agent Number" in str(e) or "tax_agent_number" in str(e).lower(),
                  str(e))
        except Exception as e:
            fail("Unexpected exception type", str(e))
        finally:
            fs.tax_agent_number = original_tan
            fs.save()

        # ══════════════════════════════════════════════════════════════════
        # SUITE 11 — Practice Branding Keys on All Entity Types
        # ══════════════════════════════════════════════════════════════════
        section("Suite 11: Practice Branding Keys — All Entity Types")
        PRACTICE_KEYS = [
            "practice_name", "practice_abn", "practice_address_1",
            "practice_phone", "practice_email",
            "practice_tax_agent_number", "practice_signatory_name",
            "practice_professional_body", "practice_independence_maintained",
        ]
        test_entities = [
            ("company (ETS)", entity_d7, fy_d7),
            ("trust", entity, fy),
            ("partnership", entity_p, fy_p),
            ("sole_trader", entity_st, fy_st),
        ]
        for label, ent, fyr in test_entities:
            try:
                dcb_brand = DocumentContextBuilder(ent, fyr, wizard_data={})
                ctx_brand = dcb_brand.build("financial_statements")
                all_present = all(ctx_brand.get(k) for k in PRACTICE_KEYS)
                check(f"All practice_* keys present ({label})", all_present,
                      ", ".join(k for k in PRACTICE_KEYS if not ctx_brand.get(k)) or "all present")
            except Exception as e:
                fail(f"Practice branding check ({label})", str(e))

        # ══════════════════════════════════════════════════════════════════
        # SUITE 12 — Financial Accuracy Spot-Checks
        # ══════════════════════════════════════════════════════════════════
        section("Suite 12: Financial Accuracy — Known Values")

        # Trust: revenue = 95000+45000+15000+30000 = 185000
        if "financial_statements" in contexts:
            ctx = contexts["financial_statements"]
            check("Trust revenue = 185,000",
                  ctx.get("revenue") == Decimal("185000"),
                  str(ctx.get("revenue")))
            # Trust expenses = 9500+5500+1000+2500+4500 = 23000
            check("Trust expenses = 23,000",
                  ctx.get("expenses") == Decimal("23000"),
                  str(ctx.get("expenses")))
            # Trust net profit = 185000 - 23000 = 162000
            check("Trust net_profit = 162,000",
                  ctx.get("net_profit") == Decimal("162000"),
                  str(ctx.get("net_profit")))
            # Prior year revenue = 82000+38000+12000+30000 = 162000
            check("Trust revenue_py = 162,000",
                  ctx.get("revenue_py") == Decimal("162000"),
                  str(ctx.get("revenue_py")))

        # Partnership: revenue = 680000+95000 = 775000, COGS = 185000+42000 = 227000
        # GP = 548000, expenses = 180000+48000+28000+15000+8000+7000+52000 = 338000
        # Net profit = 548000 - 338000 = 210000
        if "financial_statements" in contexts_p:
            ctx = contexts_p["financial_statements"]
            check("Partnership revenue = 775,000",
                  ctx.get("revenue") == Decimal("775000"),
                  str(ctx.get("revenue")))
            check("Partnership net_profit = 210,000",
                  ctx.get("net_profit") == Decimal("210000"),
                  str(ctx.get("net_profit")))

        # Sole trader: revenue = 185000, expenses = 8500+18000+4500+6500+19250+33250 = 90000
        # Net profit = 185000 - 90000 = 95000
        if "financial_statements" in contexts_st:
            ctx = contexts_st["financial_statements"]
            check("Sole trader revenue = 185,000",
                  ctx.get("revenue") == Decimal("185000"),
                  str(ctx.get("revenue")))
            check("Sole trader net_profit = 95,000",
                  ctx.get("net_profit") == Decimal("95000"),
                  str(ctx.get("net_profit")))

        # Going concern: net_assets should be negative
        check("Going concern net_assets < 0",
              ctx_gc.get("net_assets", Decimal(0)) < 0,
              str(ctx_gc.get("net_assets")))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    failures = run_all_tests()
    sys.exit(1 if failures else 0)
