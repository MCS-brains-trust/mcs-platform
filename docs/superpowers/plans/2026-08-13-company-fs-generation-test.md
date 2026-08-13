# Company FS Generation Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove `build_company_context` produces correct financial statements for a company with income tax posted, prior-year comparatives, cost of sales and trading stock — asserting every figure against a hand-computed value, not a blessed baseline.

**Architecture:** One new Django test module. A local builder emits Model A trial-balance row shapes (a `rollover` row carrying the opening and the prior-year comparative, plus a `tb_import` row carrying the movement), which is what lets the tests produce a full comparative column without performing a roll-forward. Five scenario classes assert on the context dict; a sixth renders the document and checks the totals reach it.

**Tech Stack:** Django 5 / Python 3.12, `unittest` via `manage.py test`, `python-docx` for the render check.

**Spec:** `docs/superpowers/specs/2026-08-13-company-fs-generation-test-design.md`

## Global Constraints

- **Django tests need the sqlite override.** The default database is live Postgres and `manage.py test` cannot create a test database on it. Always:
  `cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test <label> -v1`
- **The wider Django suite has a large pre-existing failure baseline.** Only run the labels named in each task.
- **Never touch production.** Nothing in this plan needs a server, a browser, or the E2E database. It is pure Django tests against sqlite.
- **Tests create their own `AccountMapping` rows.** They are not seeded by migrations in a way these tests should depend on — `core/tests_docgen_section_classification.py:92` establishes the pattern.
- **Amounts are debit-positive.** Credits are negative. This is the convention `_get_tb_sections` reads and every figure in this plan follows it.
- **`format_amount` output is exact**: whole dollars, comma separators, `(1,234)` for negatives, `—` for zero or None. Assertions compare against those strings verbatim.
- **Do not add a gross profit line.** The merge of cost of sales into Expenses is intended (decision of 2026-08-13) and is pinned by Task 4. No `.docx` template work is in scope.

---

## Reference: verified facts

Established by reading the source and querying the production copy on 2026-08-13. Relied on by the tasks below.

**Context keys.** Only these raw values are exposed; everything else is a formatted string:
`_income_tax_cy`, `_income_tax_py`, `_has_income_tax`, `_total_revenue_cy`, `_total_revenue_py`, `_sections`, `_entity`, `_fy`, `_note_map`, `_note_lookup`.

There is **no** `_net_profit_cy`. Profit is asserted through the formatted `net_profit_cy` / `net_profit_pretax_cy`.

**Formatted keys used by this plan:** `total_income_cy/py`, `total_expenses_cy/py`, `total_cogs_cy/py`, `gross_profit_cy/py`, `net_profit_pretax_cy/py`, `income_tax_cy/py`, `net_profit_cy/py`, `total_current_assets_cy/py`, `total_noncurrent_assets_cy/py`, `total_assets_cy/py`, `total_liabilities_cy/py`, `net_assets_cy/py`, `total_equity_cy/py`, `retained_profit_opening_cy/py`, `retained_profit_closing_cy/py`, plus the booleans `has_trading` and `has_income_tax`.

**`income_tax_cy` is `format_amount(-income_tax_cy_raw)`.** Tax is a debit, so the raw value is positive and the displayed value is bracketed. A *positive* displayed tax figure means tax is being added to profit — that was the `ac69078` defect.

**Section routing.** Account code decides the section, except below 1000 where the mapped `standard_code` decides:
`IS-REV-001` → `trading_income`; other `IS-REV-*` → `income`; `IS-COS-*` → `cogs`; `IS-EXP-*` → `expenses`.
By code: `<1200` → `cogs`; `<2000` → `expenses`; `<2500` → `current_assets`; `<3000` → `noncurrent_assets`; `<3500` → `current_liabilities`; `<4000` → `noncurrent_liabilities`; `<5000` → **`equity`** (regardless of the account's own section).

**Retained profit roll-forward.** Accounts in the equity section whose name contains `retained` or `accumulated` supply the opening balance: `retained_profit_opening_cy = -cy_amount`, and `retained_profit_closing_cy = opening + net_profit_cy - dividends`. `2869 Less: Accumulated depreciation` contains "accumulated" but sits in `noncurrent_assets`, so it is not scanned.

**`generate_financial_statements(financial_year_id)`** (`core/fs_template_service.py:4127`) returns `dict[document_type, BytesIO]` of rendered **DOCX** buffers. PDF conversion is a separate LibreOffice step downstream and is deliberately out of scope — it is slow and needs an external binary, and the figures are already in the DOCX.

---

## File Structure

**Created:**
- `core/tests_fs_company_generation.py` — the builder, the shared chart and figures, the invariant helper, and all six scenario classes. One file: the builder encodes assumptions specific to these fixtures, and splitting it out would invite reuse that hides them.

**Modified:** none. This plan adds tests only.

---

### Task 1: The builder and the chart

**Files:**
- Create: `core/tests_fs_company_generation.py`

**Interfaces:**
- Produces: `build_company_fy(rows, *, with_prior=True, entity_name=...) -> FinancialYear`; `MAXIMAL_ROWS`; `mapping_for(standard_code) -> AccountMapping`

- [ ] **Step 1: Write the failing test**

Create `core/tests_fs_company_generation.py`:

```python
"""A company's financial statements, asserted figure by figure.

The 2026-08-13 exploratory test generated statements for a company with no tax
posted, no prior year, no cost of sales and no trading stock -- four branches of
build_company_context that never ran. This module covers them.

Every expected figure here is hand-computed from the trial balance and written out
in the docstring of the test that asserts it. That is deliberate: the two defects
this area has produced (7e11395, ac69078) both kept the balance sheet balancing
while being wrong, so a golden baseline would have blessed them and an
invariant-only test would have passed them.

Amounts are debit-positive; credits are negative, matching what _get_tb_sections
reads. See docs/superpowers/specs/2026-08-13-company-fs-generation-test-design.md.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.fs_template_service import build_company_context
from core.models import (
    AccountMapping,
    Client,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Standard line items the fixtures map to. Created per-test rather than relied on
# from a migration, following core/tests_docgen_section_classification.py.
MAPPINGS = [
    ("IS-REV-001", "Revenue", "income_statement", "Revenue"),
    ("IS-REV-002", "Other income", "income_statement", "Revenue"),
    ("IS-COS-002", "Opening stock", "income_statement", "Cost of Sales"),
    ("IS-COS-003", "Purchases", "income_statement", "Cost of Sales"),
    ("IS-COS-004", "Closing stock", "income_statement", "Cost of Sales"),
    ("IS-EXP-001", "Accounting and professional fees", "income_statement", "Expenses"),
    ("IS-EXP-007", "Depreciation and amortisation", "income_statement", "Expenses"),
    ("IS-EXP-008", "Employee benefits expense", "income_statement", "Expenses"),
    ("BS-CA-001", "Cash and cash equivalents", "balance_sheet", "Current Assets"),
    ("BS-CA-002", "Trade and other receivables", "balance_sheet", "Current Assets"),
    ("BS-CA-003", "Inventories", "balance_sheet", "Current Assets"),
    ("BS-NCA-001", "Property, plant and equipment", "balance_sheet", "Non-Current Assets"),
    ("BS-CL-001", "Trade and other payables", "balance_sheet", "Current Liabilities"),
    ("BS-EQ-001", "Issued capital", "balance_sheet", "Equity"),
    ("BS-EQ-002", "Retained earnings", "balance_sheet", "Equity"),
    ("BS-EQ-011", "Income tax provision", "balance_sheet", "Equity"),
]

# The maximal fixture: one company, two years, internally consistent.
# (code, name, standard_code, cy_amount, py_amount) -- debit-positive.
#
# Every code was verified against the production copy. Codes 0570, 1100, 1115 and
# 1130 are entity accounts rather than company-template accounts: the company
# template's cost of sales block holds only 1000 and 1126, so real companies add
# these themselves, as Berwick Mechanical Services did with 1115 Purchases.
MAXIMAL_ROWS = [
    ("0510", "Sales", "IS-REV-001", Decimal("-900000"), Decimal("-800000")),
    ("0570", "Insurance recoveries", "IS-REV-002", Decimal("-20000"), Decimal("-10000")),
    ("1100", "Opening stock", "IS-COS-002", Decimal("60000"), Decimal("50000")),
    ("1115", "Purchases", "IS-COS-003", Decimal("400000"), Decimal("360000")),
    ("1130", "Closing stock", "IS-COS-004", Decimal("-70000"), Decimal("-60000")),
    ("1510", "Accountancy", "IS-EXP-001", Decimal("10000"), Decimal("9000")),
    ("1615", "Depreciation - Plant", "IS-EXP-007", Decimal("30000"), Decimal("28000")),
    ("1965", "Wages", "IS-EXP-008", Decimal("250000"), Decimal("230000")),
    ("4110", "Income tax expense/income", "BS-EQ-011", Decimal("60000"), Decimal("50000")),
    ("2000", "Cash at bank", "BS-CA-001", Decimal("210000"), Decimal("150000")),
    ("2101", "Trade debtors", "BS-CA-002", Decimal("120000"), Decimal("100000")),
    ("2363", "Finished goods - At real value", "BS-CA-003", Decimal("70000"), Decimal("60000")),
    ("2860", "Plant & equipment (cost)", "BS-NCA-001", Decimal("300000"), Decimal("300000")),
    ("2869", "Less: Accumulated depreciation", "BS-NCA-001", Decimal("-120000"), Decimal("-90000")),
    ("3048", "Trade creditors", "BS-CL-001", Decimal("-95000"), Decimal("-80000")),
    ("4200", "Issued & paid up capital", "BS-EQ-001", Decimal("-100000"), Decimal("-100000")),
    ("4199", "Retained profits", "BS-EQ-002", Decimal("-205000"), Decimal("-197000")),
]


def mapping_for(standard_code):
    return AccountMapping.objects.get(standard_code=standard_code)


def build_company_fy(rows, *, with_prior=True, entity_name="FS Gen Test Co Pty Ltd"):
    """Create a company, its two financial years, and Model A trial balance rows.

    Model A row shapes (commit cb00bf1) are what make comparatives possible without
    performing a roll-forward:

      rollover row   closing_balance = the opening balance (prior-year closing for a
                     balance-sheet account, 0 for P&L), and prior_debit/prior_credit
                     carry the prior-year figures that become the comparative column
      tb_import row  closing_balance = the year's movement

    _get_tb_sections sums closing_balance across both, so CY = opening + movement,
    and reads PY from prior_debit - prior_credit on the rollover row.

    with_prior=False omits the rollover row entirely, modelling a first-year entity
    with no comparatives.
    """
    for code, label, statement, section in MAPPINGS:
        AccountMapping.objects.get_or_create(
            standard_code=code,
            defaults={
                "line_item_label": label,
                "financial_statement": statement,
                "statement_section": section,
            },
        )

    client = Client.objects.create(name=f"{entity_name} Client")
    entity = Entity.objects.create(
        entity_name=entity_name,
        entity_type="company",
        client=client,
        abn="11000000560",
    )
    prior_fy = FinancialYear.objects.create(
        entity=entity,
        year_label="2025",
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        status=FinancialYear.Status.FINALISED,
    )
    fy = FinancialYear.objects.create(
        entity=entity,
        year_label="2026",
        start_date=date(2025, 7, 1),
        end_date=date(2026, 6, 30),
        status=FinancialYear.Status.DRAFT,
        prior_year=prior_fy,
    )

    for code, name, standard_code, cy, py in rows:
        mapping = mapping_for(standard_code)
        EntityChartOfAccount.objects.get_or_create(
            entity=entity,
            account_code=code,
            defaults={"account_name": name, "is_active": True},
        )
        is_balance_sheet = standard_code.startswith("BS-")
        opening = py if is_balance_sheet else Decimal("0")

        if with_prior:
            TrialBalanceLine.objects.create(
                financial_year=fy,
                account_code=code,
                account_name=name,
                mapped_line_item=mapping,
                closing_balance=opening,
                debit=opening if opening > 0 else Decimal("0"),
                credit=-opening if opening < 0 else Decimal("0"),
                prior_debit=py if py > 0 else Decimal("0"),
                prior_credit=-py if py < 0 else Decimal("0"),
                source="rollover",
                is_adjustment=False,
            )
            movement = cy - opening
        else:
            movement = cy

        TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code=code,
            account_name=name,
            mapped_line_item=mapping,
            closing_balance=movement,
            debit=movement if movement > 0 else Decimal("0"),
            credit=-movement if movement < 0 else Decimal("0"),
            prior_debit=Decimal("0"),
            prior_credit=Decimal("0"),
            source="tb_import",
            is_adjustment=False,
        )

    return fy


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BuilderTests(TestCase):
    """The builder must reproduce the input figures exactly, or every scenario
    below is testing the wrong numbers."""

    def test_the_maximal_trial_balance_sums_to_zero_in_both_years(self):
        cy = sum(r[3] for r in MAXIMAL_ROWS)
        py = sum(r[4] for r in MAXIMAL_ROWS)
        self.assertEqual(cy, Decimal("0"), "current-year trial balance does not balance")
        self.assertEqual(py, Decimal("0"), "prior-year trial balance does not balance")

    def test_the_builder_reproduces_each_accounts_cy_and_py(self):
        fy = build_company_fy(MAXIMAL_ROWS)
        context = build_company_context(fy)

        by_code = {}
        for items in context["_sections"].values():
            for item in items:
                by_code[item["account_code"]] = item

        for code, _name, _std, cy, py in MAXIMAL_ROWS:
            # 4110 is deliberately absent from _sections. build_company_context
            # extracts the income tax appropriation OUT of the equity section
            # before returning, so it reaches no section at all; its figures are
            # asserted through _income_tax_cy just below.
            if code == "4110":
                continue
            with self.subTest(code=code):
                self.assertIn(code, by_code, f"{code} did not reach any section")
                self.assertEqual(by_code[code]["cy_amount"], cy)
                self.assertEqual(by_code[code]["py_amount"], py)

        self.assertEqual(context["_income_tax_cy"], Decimal("60000"))
        self.assertEqual(context["_income_tax_py"], Decimal("50000"))

    def test_the_equity_section_carries_an_injected_current_year_profit_row(self):
        """Under the unclosed-TB convention the equity accounts hold only opening
        balances, so build_company_context injects a synthetic
        'Current year profit / (loss)' row (account_code 'NET_PROFIT') to make
        equity reconcile to net assets. Anything summing the equity section must
        not then add the profit a second time."""
        context = build_company_context(build_company_fy(MAXIMAL_ROWS))
        injected = [i for i in context["_sections"]["equity"]
                    if i["account_code"] == "NET_PROFIT"]
        self.assertEqual(len(injected), 1)
        self.assertEqual(injected[0]["cy_amount"], Decimal("-180000"))

    def test_without_prior_the_comparative_column_is_zero(self):
        fy = build_company_fy(MAXIMAL_ROWS, with_prior=False)
        context = build_company_context(fy)

        for items in context["_sections"].values():
            for item in items:
                with self.subTest(code=item["account_code"]):
                    self.assertEqual(item["py_amount"], Decimal("0"))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation -v1
```

Expected: the module imports (the builder is defined in the same file), and
`test_the_builder_reproduces_each_accounts_cy_and_py` either passes or fails on a
specific account. If it fails, the builder's row shapes are wrong — fix the builder,
not the expectation. `test_the_maximal_trial_balance_sums_to_zero_in_both_years`
must pass immediately; if it does not, the figures were mistyped.

- [ ] **Step 3: Fix any row-shape mismatch**

The likely failure is a balance-sheet account whose CY comes back as the movement
rather than the full closing balance. That means the rollover row's
`closing_balance` was not set to the opening. Re-read the Model A table in the
builder docstring.

- [ ] **Step 4: Run again to confirm all three pass**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation -v1
```

Expected: `Ran 3 tests ... OK`

- [ ] **Step 5: Commit**

```bash
cd /opt/statementhub && git add core/tests_fs_company_generation.py
git commit -m "test: Model A trial balance builder for company FS generation tests

Emits the rollover + tb_import row pair that lets these tests produce a full
comparative column without performing a roll-forward."
```

---

### Task 2: Income tax posted

**Files:**
- Modify: `core/tests_fs_company_generation.py`

**Interfaces:**
- Consumes: `build_company_fy`, `MAXIMAL_ROWS` from Task 1

- [ ] **Step 1: Write the failing test**

Append:

```python
@override_settings(STORAGES=STORAGES_OVERRIDE)
class IncomeTaxTests(TestCase):
    """4110 mapped to BS-EQ-011 is the tax journal.

    Hand-computed, current year:
        income          900,000 + 20,000            = 920,000
        cost of sales    60,000 + 400,000 - 70,000  = 390,000
        other expenses   10,000 + 30,000 + 250,000  = 290,000
        profit pretax   920,000 - 390,000 - 290,000 = 240,000
        income tax                                     60,000
        profit after tax        240,000 - 60,000    = 180,000

    The ac69078 defect made profit after tax EXCEED profit before tax, because a
    non-tax equity account was swept into the tax figure with a credit balance.
    """

    def setUp(self):
        self.context = build_company_context(build_company_fy(MAXIMAL_ROWS))

    def test_the_tax_journal_is_recognised(self):
        self.assertTrue(self.context["has_income_tax"])
        self.assertEqual(self.context["_income_tax_cy"], Decimal("60000"))
        self.assertEqual(self.context["_income_tax_py"], Decimal("50000"))

    def test_tax_is_deducted_from_profit_not_added(self):
        self.assertEqual(self.context["net_profit_pretax_cy"], "240,000")
        self.assertEqual(self.context["net_profit_cy"], "180,000")

    def test_the_tax_line_displays_as_a_deduction(self):
        """format_amount(-60000) -- brackets mean deducted. A positive figure here
        is the ac69078 symptom."""
        self.assertEqual(self.context["income_tax_cy"], "(60,000)")
        self.assertEqual(self.context["income_tax_py"], "(50,000)")

    def test_the_tax_account_is_removed_from_equity(self):
        equity_codes = {i["account_code"] for i in self.context["_sections"]["equity"]}
        self.assertNotIn("4110", equity_codes)
        self.assertIn("4200", equity_codes)
        self.assertIn("4199", equity_codes)

    def test_the_retained_profit_appropriation_reconciles(self):
        """opening 205,000 + after-tax profit 180,000 = closing 385,000"""
        self.assertEqual(self.context["retained_profit_opening_cy"], "205,000")
        self.assertEqual(self.context["retained_profit_closing_cy"], "385,000")
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation.IncomeTaxTests -v1
```

Expected: PASS. The behaviour was fixed in `ac69078`; these tests pin it at the
context level, where `core/tests_fs_income_tax_classification.py` pins the predicate.
**If any fail, do not adjust the expectation** — recompute by hand from the docstring
first and treat a genuine mismatch as a defect to report.

- [ ] **Step 3: Commit**

```bash
cd /opt/statementhub && git add core/tests_fs_company_generation.py
git commit -m "test: income tax posted, deducted not added, excluded from equity"
```

---

### Task 3: Prior-year comparatives

**Files:**
- Modify: `core/tests_fs_company_generation.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
@override_settings(STORAGES=STORAGES_OVERRIDE)
class ComparativeColumnTests(TestCase):
    """The prior column, which a first-year entity never reaches.

    Hand-computed, prior year:
        income          800,000 + 10,000            = 810,000
        cost of sales    50,000 + 360,000 - 60,000  = 350,000
        other expenses    9,000 + 28,000 + 230,000  = 267,000
        profit pretax   810,000 - 350,000 - 267,000 = 193,000
        income tax                                     50,000
        profit after tax        193,000 - 50,000    = 143,000
        net assets  150,000+100,000+60,000+300,000-90,000-80,000 = 440,000
        equity      100,000 + 197,000 + 143,000                  = 440,000
    """

    def setUp(self):
        self.context = build_company_context(build_company_fy(MAXIMAL_ROWS))

    def test_prior_year_profit_and_loss(self):
        self.assertEqual(self.context["total_income_py"], "810,000")
        self.assertEqual(self.context["total_cogs_py"], "350,000")
        self.assertEqual(self.context["total_expenses_py"], "617,000")
        self.assertEqual(self.context["net_profit_pretax_py"], "193,000")
        self.assertEqual(self.context["net_profit_py"], "143,000")

    def test_prior_year_balance_sheet(self):
        self.assertEqual(self.context["total_current_assets_py"], "310,000")
        self.assertEqual(self.context["total_noncurrent_assets_py"], "210,000")
        self.assertEqual(self.context["total_assets_py"], "520,000")
        self.assertEqual(self.context["total_liabilities_py"], "80,000")
        self.assertEqual(self.context["net_assets_py"], "440,000")
        self.assertEqual(self.context["total_equity_py"], "440,000")

    def test_prior_retained_profit_reconciles(self):
        self.assertEqual(self.context["retained_profit_opening_py"], "197,000")
        self.assertEqual(self.context["retained_profit_closing_py"], "340,000")

    def test_a_first_year_entity_reports_no_comparatives(self):
        context = build_company_context(build_company_fy(MAXIMAL_ROWS, with_prior=False))
        self.assertEqual(context["total_income_py"], "—")
        self.assertEqual(context["net_assets_py"], "—")
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation.ComparativeColumnTests -v1
```

Expected: PASS. `format_amount` renders zero as `—`, which is why the first-year
assertions compare against that and not `"0"`.

- [ ] **Step 3: Commit**

```bash
cd /opt/statementhub && git add core/tests_fs_company_generation.py
git commit -m "test: prior-year comparative column across P&L and balance sheet"
```

---

### Task 4: Trading income, cost of sales, and the pinned merge

**Files:**
- Modify: `core/tests_fs_company_generation.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
@override_settings(STORAGES=STORAGES_OVERRIDE)
class TradingAndCostOfSalesTests(TestCase):
    """0510 maps to IS-REV-001 (trading income); the stock and purchase lines map
    to IS-COS-*, which sets has_trading and exercises the merge branch.

    The merge is deliberate and is pinned here: a trading company's detailed P&L
    shows a single Income and a single Expenses section, with cost of sales folded
    into Expenses and NO gross profit line. Confirmed on Berwick Mechanical Services
    FY2017, whose Purchases 210,029 renders under Expenses. Decision of 2026-08-13.
    If the presentation is ever meant to change, this test changes with it -- it
    records current intent, not an accounting standard.

        cost of sales   60,000 + 400,000 - 70,000 = 390,000
        other expenses  10,000 + 30,000 + 250,000 = 290,000
        rendered total expenses                    = 680,000
    """

    def setUp(self):
        self.context = build_company_context(build_company_fy(MAXIMAL_ROWS))

    def test_trading_is_detected(self):
        self.assertTrue(self.context["has_trading"])

    def test_trading_income_and_other_income_are_separated_in_the_sections(self):
        trading = {i["account_code"] for i in self.context["_sections"]["trading_income"]}
        other = {i["account_code"] for i in self.context["_sections"]["income"]}
        self.assertEqual(trading, {"0510"})
        self.assertEqual(other, {"0570"})

    def test_cost_of_sales_totals_correctly(self):
        self.assertEqual(self.context["total_cogs_cy"], "390,000")

    def test_cost_of_sales_is_merged_into_rendered_expenses(self):
        """680,000 = cost of sales 390,000 + other expenses 290,000."""
        self.assertEqual(self.context["total_expenses_cy"], "680,000")
        rendered_codes = {row["account_code"] for row in self.context["expenses"]}
        for code in ("1100", "1115", "1130"):
            self.assertIn(code, rendered_codes, "cost of sales did not merge into Expenses")

    def test_total_income_merges_trading_and_other_income(self):
        self.assertEqual(self.context["total_income_cy"], "920,000")
        rendered_codes = {row["account_code"] for row in self.context["income"]}
        self.assertEqual(rendered_codes, {"0510", "0570"})

    def test_gross_profit_is_computed_but_not_presented(self):
        """900,000 trading income - 390,000 cost of sales = 510,000. The value is
        exposed to templates; no .docx renders it, and none should."""
        self.assertEqual(self.context["gross_profit_cy"], "510,000")
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation.TradingAndCostOfSalesTests -v1
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /opt/statementhub && git add core/tests_fs_company_generation.py
git commit -m "test: trading income, cost of sales, and the pinned merge into Expenses"
```

---

### Task 5: Trading stock

**Files:**
- Modify: `core/tests_fs_company_generation.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
@override_settings(STORAGES=STORAGES_OVERRIDE)
class TradingStockTests(TestCase):
    """The stock cycle, and its tie to the balance sheet.

        opening stock              60,000   (prior year's closing stock)
        purchases                 400,000
        less closing stock        (70,000)
        cost of sales             390,000

    Closing stock 70,000 equals finished goods carried on the balance sheet, and
    prior closing stock 60,000 equals both the prior carrying amount and the
    current year's opening stock. Those ties make the fixture self-checking.
    """

    def setUp(self):
        self.context = build_company_context(build_company_fy(MAXIMAL_ROWS))
        self.by_code = {}
        for items in self.context["_sections"].values():
            for item in items:
                self.by_code[item["account_code"]] = item

    def test_the_stock_cycle_produces_cost_of_sales(self):
        opening = self.by_code["1100"]["cy_amount"]
        purchases = self.by_code["1115"]["cy_amount"]
        closing = self.by_code["1130"]["cy_amount"]
        self.assertEqual(opening + purchases + closing, Decimal("390000"))
        self.assertEqual(self.context["total_cogs_cy"], "390,000")

    def test_finished_goods_is_a_current_asset(self):
        codes = {i["account_code"] for i in self.context["_sections"]["current_assets"]}
        self.assertIn("2363", codes)
        self.assertEqual(self.context["total_current_assets_cy"], "400,000")

    def test_closing_stock_ties_to_the_balance_sheet_carrying_amount(self):
        closing_stock = -self.by_code["1130"]["cy_amount"]
        on_hand = self.by_code["2363"]["cy_amount"]
        self.assertEqual(closing_stock, on_hand)

    def test_prior_closing_stock_ties_to_current_opening_stock(self):
        prior_closing = -self.by_code["1130"]["py_amount"]
        current_opening = self.by_code["1100"]["cy_amount"]
        self.assertEqual(prior_closing, current_opening)
        self.assertEqual(prior_closing, self.by_code["2363"]["py_amount"])
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation.TradingStockTests -v1
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /opt/statementhub && git add core/tests_fs_company_generation.py
git commit -m "test: trading stock cycle and its tie to the balance sheet"
```

---

### Task 6: The full set and the invariants

**Files:**
- Modify: `core/tests_fs_company_generation.py`

**Interfaces:**
- Produces: `assert_statements_are_internally_consistent(test_case, context)`

- [ ] **Step 1: Write the failing test**

Append:

```python
def assert_statements_are_internally_consistent(t, context):
    """Four relationships that must hold for any company, whatever the figures.

    The third is the one the ac69078 defect broke while the balance sheet still
    balanced, which is why an invariant-only suite would have passed it and why
    these run alongside hand-computed figures rather than instead of them.
    """
    sections = context["_sections"]

    def total(key):
        return sum((i["cy_amount"] or Decimal("0")) for i in sections[key])

    assets = total("current_assets") + total("noncurrent_assets")
    liabilities = -(total("current_liabilities") + total("noncurrent_liabilities"))
    # The equity section already contains the injected 'Current year profit /
    # (loss)' row (account_code 'NET_PROFIT'), which build_company_context adds
    # because the trial balance is unclosed. Adding the profit again here would
    # double-count it.
    equity = -total("equity")

    t.assertEqual(assets - liabilities, equity,
                  "net assets do not equal total equity")
    t.assertEqual(context["net_assets_cy"], context["total_equity_cy"],
                  "the rendered net assets and total equity disagree")

    pretax = _money(context["net_profit_pretax_cy"])
    after = _money(context["net_profit_cy"])
    t.assertEqual(after, pretax - context["_income_tax_cy"],
                  "profit after tax is not profit before tax less income tax")


def _money(formatted):
    """Turn a format_amount string back into a Decimal."""
    if formatted in ("—", "-"):
        return Decimal("0")
    negative = formatted.startswith("(")
    digits = formatted.strip("()").replace(",", "")
    return Decimal(digits) * (-1 if negative else 1)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class FullSetTests(TestCase):
    """Everything at once. The interactions are where both prior defects lived.

    Current year, hand-computed:
        income      900,000 + 20,000                        =   920,000
        cost of sales  60,000 + 400,000 - 70,000            =   390,000
        expenses    10,000 + 30,000 + 250,000               =   290,000
        pretax      920,000 - 680,000                       =   240,000
        tax                                                 =    60,000
        after tax   240,000 - 60,000                        =   180,000
        current assets  210,000 + 120,000 + 70,000          =   400,000
        non-current     300,000 - 120,000                   =   180,000
        total assets                                        =   580,000
        liabilities                                         =    95,000
        net assets  580,000 - 95,000                        =   485,000
        equity      100,000 + 205,000 + 180,000             =   485,000
    """

    def setUp(self):
        self.context = build_company_context(build_company_fy(MAXIMAL_ROWS))

    def test_the_profit_and_loss(self):
        self.assertEqual(self.context["total_income_cy"], "920,000")
        self.assertEqual(self.context["total_cogs_cy"], "390,000")
        self.assertEqual(self.context["total_expenses_cy"], "680,000")
        self.assertEqual(self.context["net_profit_pretax_cy"], "240,000")
        self.assertEqual(self.context["income_tax_cy"], "(60,000)")
        self.assertEqual(self.context["net_profit_cy"], "180,000")

    def test_the_balance_sheet(self):
        self.assertEqual(self.context["total_current_assets_cy"], "400,000")
        self.assertEqual(self.context["total_noncurrent_assets_cy"], "180,000")
        self.assertEqual(self.context["total_assets_cy"], "580,000")
        self.assertEqual(self.context["total_liabilities_cy"], "95,000")
        self.assertEqual(self.context["net_assets_cy"], "485,000")
        self.assertEqual(self.context["total_equity_cy"], "485,000")

    def test_depreciation_expense_matches_the_accumulated_movement(self):
        """30,000 charged for the year; accumulated moves 90,000 -> 120,000."""
        by_code = {}
        for items in self.context["_sections"].values():
            for item in items:
                by_code[item["account_code"]] = item
        charge = by_code["1615"]["cy_amount"]
        movement = -(by_code["2869"]["cy_amount"] - by_code["2869"]["py_amount"])
        self.assertEqual(charge, movement)

    def test_the_invariants_hold(self):
        assert_statements_are_internally_consistent(self, self.context)
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation.FullSetTests -v1
```

Expected: PASS. The equity section already carries the injected `NET_PROFIT` row, so
`assert_statements_are_internally_consistent` must NOT add the profit again —
Task 1's `test_the_equity_section_carries_an_injected_current_year_profit_row`
documents that. If the equity comparison fails by exactly the profit figure
(180,000), the helper is double-counting.

- [ ] **Step 3: Add the invariant call to the earlier scenarios**

Add `assert_statements_are_internally_consistent(self, self.context)` as a final
assertion inside `IncomeTaxTests.test_the_tax_journal_is_recognised`,
`TradingAndCostOfSalesTests.test_trading_is_detected`, and
`TradingStockTests.test_the_stock_cycle_produces_cost_of_sales`, so every scenario
carries the invariants as the spec requires.

- [ ] **Step 4: Run the whole module**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation -v1
```

Expected: all classes pass.

- [ ] **Step 5: Commit**

```bash
cd /opt/statementhub && git add core/tests_fs_company_generation.py
git commit -m "test: the full company set, with accounting invariants applied throughout"
```

---

### Task 7: The figures reach the document

**Files:**
- Modify: `core/tests_fs_company_generation.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
@override_settings(STORAGES=STORAGES_OVERRIDE)
class DocumentRenderTests(TestCase):
    """A correct figure that never reaches the page is still a broken statement.

    generate_financial_statements returns rendered DOCX buffers keyed by document
    type; PDF conversion is a separate LibreOffice step downstream and is not
    covered here -- it needs an external binary and adds no figure coverage.
    """

    @staticmethod
    def _text_of(buffer):
        import docx

        buffer.seek(0)
        document = docx.Document(buffer)
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.extend(cell.text for cell in row.cells)
        return "\n".join(parts)

    def setUp(self):
        from core.fs_template_service import generate_financial_statements

        fy = build_company_fy(MAXIMAL_ROWS)
        self.documents = generate_financial_statements(fy.pk)
        self.assertTrue(self.documents, "no documents were rendered at all")

    def test_the_profit_and_loss_totals_reach_the_document(self):
        """Document types are BALANCE_SHEET, COMPILATION, COVER, DECLARATION,
        DETAILED_PL, NOTES, SUMMARY_PL."""
        rendered = self._text_of(self.documents["DETAILED_PL"])
        for figure in ("920,000", "680,000", "240,000", "(60,000)", "180,000"):
            with self.subTest(figure=figure):
                self.assertIn(figure, rendered,
                              f"{figure} was computed but never reached the P&L")

    def test_the_balance_sheet_totals_reach_the_document(self):
        rendered = self._text_of(self.documents["BALANCE_SHEET"])
        for figure in ("400,000", "580,000", "95,000", "485,000"):
            with self.subTest(figure=figure):
                self.assertIn(figure, rendered,
                              f"{figure} was computed but never reached the balance sheet")

    def test_the_notes_tie_to_the_face_of_the_statements(self):
        """The spec's fourth invariant, asserted where the notes actually exist.

        Receivables 120,000 and plant 300,000 each appear in the notes AND on the
        balance sheet -- which is what "ties" means here. Asserted against the
        labelled line ("Trade debtors\\n120,000", "Plant & equipment (cost)\\n300,000"),
        not the bare figure, since the bare figure also matches unrelated text (the
        PPE note's accumulated depreciation renders "(120,000)", which contains
        "120,000" as a substring).

        Inventories is deliberately NOT asserted here. The application generates no
        inventories note at all: `_compute_note_map` emits exactly six note types
        (policies, receivables, ppe, related_party, income_tax, events), and
        `fs_template_service.py` has no inventories note branch. A company carrying
        trading stock gets an Inventories line on the balance sheet with no
        supporting note. Confirmed 2026-08-13 and accepted as out of scope for this
        test module, which changes no production code.
        """
        notes = self._text_of(self.documents["NOTES"])
        balance_sheet = self._text_of(self.documents["BALANCE_SHEET"])
        self.assertIn("Trade debtors\n120,000", notes,
                      "the receivables note's labelled line is missing")
        self.assertIn("Plant & equipment (cost)\n300,000", notes,
                      "the PPE note's labelled line is missing")
        for figure in ("120,000", "300,000"):
            with self.subTest(figure=figure):
                self.assertIn(figure, balance_sheet,
                              f"{figure} is in the notes but not on the balance sheet")
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_fs_company_generation.DocumentRenderTests -v1
```

Expected: PASS. If `documents` is empty, the `FinancialStatementTemplate` rows or
their `.docx` files are missing from the test database — the migration that creates
them logs `CREATED <TYPE>/company` lines during test setup. If only some figures are
missing, check whether the missing one belongs to a document type this entity does
not generate rather than assuming a defect.

- [ ] **Step 3: Commit**

```bash
cd /opt/statementhub && git add core/tests_fs_company_generation.py
git commit -m "test: the computed totals reach the rendered document"
```

---

## Verification

Complete when all of these hold:

1. `core.tests_fs_company_generation` passes in full.
2. The ten existing modules still pass:
   ```bash
   cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test \
     core.tests_fs_income_tax_classification core.tests_fs_template_service_bs_aggregation \
     core.tests_fs_template_service_cash_classification core.tests_fs_template_service_depreciation_report \
     core.tests_fs_template_service_nca_suppression core.tests_fs_template_service_section_classification \
     core.tests_docgen_bs_aggregation core.tests_docgen_bs_sub_classification \
     core.tests_docgen_overdraft_reclassification core.tests_docgen_section_classification -v1
   ```
3. No production code was modified — this plan adds tests only. If a scenario fails
   on a genuine defect, stop and report it rather than changing the expectation to
   match the output.
