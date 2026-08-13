# A comprehensive company financial-statement generation test

## Purpose

Generating a full set of statements for a fake company (2026-08-13) found one real
defect — income tax classified by account code alone, fixed in `ac69078` — and proved
the arithmetic correct for the case tested. But that case was narrow: no income tax
posted, no prior year, no cost of sales, no trading stock. Four substantial branches of
`build_company_context` were never executed.

This test closes that gap. It is not a smoke test: every figure is asserted against a
number worked out by hand from the trial balance, so a *wrong* figure fails, not merely
a *changed* one.

## Why a wrong figure matters more than a changed one

The two defects this area has produced were both invisible to a balance check:

- The retained-profits defect (`7e11395`) kept total equity right while splitting it
  across two accounts.
- The income-tax defect (`ac69078`) moved an equity account into the tax line and back
  into equity via profit, so Total Equity still equalled Net Assets. The statements
  balanced and were wrong by $62,189.

A golden baseline would have blessed both. An invariant-only test would have passed
both. Hand-computed figures plus invariants catch both, which is why that combination
is the oracle here.

## Decisions

**Assert on `build_company_context()`, not on the PDF.** It exposes raw `Decimal`
values (`_income_tax_cy`, `_net_profit_cy`), runs without file I/O, and is stable
against layout change. One separate test renders a PDF end to end and confirms the key
totals survive into it — enough to catch a figure that never reaches the page, without
a brittle full-text baseline.

**A shared builder plus focused scenarios plus one maximal case.** Focused tests
localise a failure to a single feature; the maximal one exercises the interactions,
which is where both previous defects lived.

**The gross-profit presentation is pinned as-is.** A trading company's statements
currently merge cost of sales into Expenses and show no gross profit line — confirmed
on Berwick Mechanical Services FY2017, which has `Purchases 210,029` rendered under
Expenses. `gross_profit_cy` is computed and passed to the template but no `.docx`
renders it. Per the 2026-08-13 decision this is intended, so the test asserts it and a
future change to that presentation fails as a regression. No template work is in scope.

**Rejected: a Tier 2 Playwright spec.** Highest fidelity, but ~90s per run, needs a
database branch, and cannot produce comparatives without first performing a
roll-forward — dragging an unrelated subsystem into a statements test. Tier 2 already
covers that the UI path works; this test is about arithmetic and presentation.

## Model A row shapes

Comparatives are the reason this test can be written at all without a roll-forward.
Under the Model A shape (commit `cb00bf1`, 2026-05-20) each account has up to two rows:

| Source | `closing_balance` | `prior_debit` / `prior_credit` |
|---|---|---|
| `rollover` | the opening balance — prior-year closing for a balance-sheet account, `0` for P&L | the prior-year figures, which become the comparative column |
| `tb_import` | the year's movement | — |

`_get_tb_sections` sums `closing_balance` across both, so CY = opening + movement, and
reads PY from `prior_debit - prior_credit` on the rollover row. A fixture that emits
both rows therefore produces a full comparative column directly. This is the shape
`core/tests_fs_template_service_bs_aggregation.py` already documents.

## Components

### The builder

```python
build_company_fy(rows, *, with_prior=True) -> FinancialYear
```

`rows` is a list of `(account_code, account_name, standard_code, cy_amount, py_amount)`
in the debit-positive convention the reader uses — credits negative. For each row the
builder creates the `EntityChartOfAccount` entry, resolves `standard_code` to an
`AccountMapping`, and writes the rollover and `tb_import` rows above.

It lives in `core/tests_fs_company_generation.py` beside the tests rather than in a
shared helpers module: it encodes assumptions specific to this test's fixtures, and a
shared helper would invite reuse that hides those assumptions.

### The chart

Every code is taken from the production copy. Codes marked *master* are on the company
template chart (`core_chartofaccount`, `entity_type='company'`); codes marked *entity*
exist on a real company's own chart but not the template — the company template's cost
of sales block is nearly empty (`1000 Sales/Fees/Commissions` and `1126 Goods for own
use` only), so real companies add these themselves, as Berwick did with `1115`.

| Code | Name | Standard code | Source |
|---|---|---|---|
| `0510` | Sales | `IS-REV-001` | master |
| `0570` | Insurance recoveries | `IS-REV-002` | entity (Berwick) |
| `1100` | Opening stock | `IS-COS-002` | entity |
| `1115` | Purchases | `IS-COS-003` | entity (Berwick) |
| `1130` | Closing stock | `IS-COS-004` | entity |
| `1510` | Accountancy | `IS-EXP-001` | master |
| `1615` | Depreciation - Plant | `IS-EXP-007` | master |
| `1965` | Wages | `IS-EXP-008` | master |
| `2000` | Cash at bank | `BS-CA-001` | master |
| `2101` | Trade debtors | `BS-CA-002` | master |
| `2363` | Finished goods - At real value | `BS-CA-003` | master |
| `2860` | Plant & equipment (cost) | `BS-NCA-001` | master |
| `2869` | Less: Accumulated depreciation | `BS-NCA-001` | master |
| `3048` | Trade creditors | `BS-CL-001` | master |
| `4110` | Income tax expense/income | `BS-EQ-011` | master |
| `4199` | Retained profits | `BS-EQ-002` | master |
| `4200` | Issued & paid up capital | `BS-EQ-001` | master |

`4200` is deliberate. The 2026-08-13 exploratory test put issued capital at `4100`,
which no real company uses, and that unrealistic choice is what exposed the income-tax
defect. Using the real code keeps the fixture honest; `core/tests_fs_income_tax_classification.py`
already pins the `4100` case directly.

`IS-REV-001` routes `0510` to `trading_income` and `IS-COS-*` routes the stock and
purchase lines to `cogs`, which is what sets `has_trading` and exercises the merge
branch.

## The maximal fixture

One company, two years, internally consistent so every figure is derivable by hand.
Amounts are debit-positive; credits are negative.

| Code | CY | PY |
|---|---|---|
| `0510` Sales | (900,000) | (800,000) |
| `0570` Insurance recoveries | (20,000) | (10,000) |
| `1100` Opening stock | 60,000 | 50,000 |
| `1115` Purchases | 400,000 | 360,000 |
| `1130` Closing stock | (70,000) | (60,000) |
| `1510` Accountancy | 10,000 | 9,000 |
| `1615` Depreciation - Plant | 30,000 | 28,000 |
| `1965` Wages | 250,000 | 230,000 |
| `4110` Income tax | 60,000 | 50,000 |
| `2000` Cash at bank | 210,000 | 150,000 |
| `2101` Trade debtors | 120,000 | 100,000 |
| `2363` Finished goods | 70,000 | 60,000 |
| `2860` Plant & equipment | 300,000 | 300,000 |
| `2869` Accumulated depreciation | (120,000) | (90,000) |
| `3048` Trade creditors | (95,000) | (80,000) |
| `4200` Issued & paid up capital | (100,000) | (100,000) |
| `4199` Retained profits (opening) | (205,000) | (197,000) |

Both columns sum to zero.

Three internal consistencies make the fixture self-checking, and each is asserted:

- Depreciation expense 30,000 equals the accumulated-depreciation movement
  (90,000 → 120,000).
- Closing stock 70,000 equals finished goods on the balance sheet.
- Prior closing stock 60,000 equals prior finished goods, and equals current opening
  stock.

### Expected results

| | CY | PY |
|---|---|---|
| Total income | 920,000 | 810,000 |
| Cost of sales (60,000 + 400,000 − 70,000) | 390,000 | 350,000 |
| Other expenses | 290,000 | 267,000 |
| Rendered total expenses (COGS merged) | 680,000 | 617,000 |
| Profit before income tax | 240,000 | 193,000 |
| Income tax | 60,000 | 50,000 |
| Profit after income tax | 180,000 | 143,000 |
| Total current assets | 400,000 | 310,000 |
| Total non-current assets | 180,000 | 210,000 |
| Total assets | 580,000 | 520,000 |
| Total liabilities | 95,000 | 80,000 |
| Net assets | 485,000 | 440,000 |
| Total equity (100,000 + 205,000 + 180,000) | 485,000 | 440,000 |

## Scenarios

Each is a `TestCase` in `core/tests_fs_company_generation.py`, using the builder with a
minimal chart except where noted.

1. **Income tax posted.** `4110` mapped to `BS-EQ-011`. Asserts tax is *deducted*
   (profit after < profit before), the account does not appear in the equity section,
   and the appropriation reconciles. Directly guards the `ac69078` defect at the
   context level, where `tests_fs_income_tax_classification` guards the predicate.
2. **Comparatives.** Every account carries a PY figure. Asserts the PY column is
   populated throughout and that PY totals match the hand-computed prior column — the
   branch a first-year entity never reaches.
3. **Trading income and cost of sales.** Asserts `has_trading` is true, cost of sales
   totals 390,000, COGS merges into rendered expenses, and **no gross profit line is
   rendered** — the presentation pinned by the 2026-08-13 decision.
4. **Trading stock.** Asserts the opening/closing stock cycle produces the cost-of-sales
   figure, that finished goods appears in current assets under `BS-CA-003`, and that
   closing stock ties to the balance-sheet carrying amount.
5. **The full set.** The maximal fixture. Asserts every figure in the table above, both
   columns, plus all four invariants.
6. **PDF render.** Generates the document end to end for the maximal fixture and
   asserts the key totals appear in the extracted text: profit before tax, income tax,
   profit after tax, net assets, total equity. Not a full-text baseline.

## Invariants

Applied in every scenario, as a shared assertion helper:

- total assets − total liabilities = net assets
- net assets = total equity
- profit after tax = profit before tax − income tax
- each note ties to its face figure (receivables, PPE, inventories)

The third is the one the income-tax defect broke while the balance sheet still
balanced.

## Testing

Run with the sqlite override, as every Django test in this repo must:

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" \
    venv/bin/python manage.py test core.tests_fs_company_generation -v1
```

The default database is live Postgres and `manage.py test` cannot create a test
database on it, so the override is mandatory, not a convenience.

The ten existing `tests_fs_*` and `tests_docgen_*` modules must stay green; they are the
regression guard for the builder not disturbing shared behaviour.

## Risks

- **The fixture asserts what the code does today, not what an accountant would sign.**
  Pinning the gross-profit merge is a decision recorded on 2026-08-13, not an accounting
  standard. If that decision changes, this test must change with it — the test is not
  evidence the presentation is correct.
- **Entity-custom codes.** Four of the seventeen accounts are not on the company
  template chart. The fixture adds them as entity accounts, which is what real companies
  do, but it means the fixture is not reproducible by seeding the template alone.
- **The PDF render test is the slow one.** If it proves flaky under CI it should be
  marked and run separately rather than weakening the context-level assertions.

## Out of scope

`.docx` template changes; introducing a gross profit line; the `/years/<pk>/statements/`
preview page, which renders no totals at all and shares none of this logic; non-company
entity types; and the Contents page listing a Solvency Resolution and Management
Representation Letter that the generator never produces.
